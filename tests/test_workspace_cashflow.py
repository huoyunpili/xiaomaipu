from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.core.management import call_command
from django.urls import reverse

from app.integrations.models import Connection, PlatformOrder
from app.integrations.services import queue_sync, store_order
from app.workbench.models import Trade
from app.workbench.services import classify
from tests.test_workspace import payload

pytestmark = pytest.mark.django_db
NOW = datetime(2026, 9, 16, 15, tzinfo=ZoneInfo("Asia/Shanghai"))


@pytest.fixture
def connection(shop, admin_user):
    return Connection.objects.create(
        shop=shop,
        actor=admin_user,
        seller_id="cashflow-test",
        sync_start_at=NOW.replace(day=11, hour=0),
    )


def historical(connection, number, **changes):
    return store_order(
        connection,
        payload(
            number=number,
            **{
                "order_time": int((NOW - timedelta(days=9)).timestamp()),
                "pay_time": int((NOW - timedelta(days=9, minutes=-1)).timestamp()),
                "update_time": int(NOW.timestamp()),
                **changes,
            },
        ),
    )


def test_first_week_profit_and_month_sync(connection, admin_user, client):
    early = NOW.replace(day=2, hour=10)
    row = historical(
        connection,
        "95000001",
        order_time=int(early.timestamp()) - 100,
        pay_time=int(early.timestamp()) - 50,
        order_status=22,
        confirm_time=int(early.timestamp()),
    )
    trade = Trade.objects.get(platform=row)
    trade.unit_cost_fen = 6000
    trade.save()
    client.force_login(admin_user)
    response = client.get(
        reverse("wb-profits"),
        {"mode": "actual", "range": "custom", "start": "2026-09-01", "end": "2026-09-06"},
    )
    assert response.context["summary"]["count"] == 1
    assert response.context["summary"]["profit"] == trade.profit_fen
    with patch("app.integrations.services.timezone.now", return_value=NOW):
        run = queue_sync(connection, full=True)
    assert run.window_start == int(NOW.replace(day=1, hour=0, minute=0, second=0).timestamp())


def test_carryover_refunds_and_today_completion_are_visible(connection, admin_user, client):
    refunds = [
        historical(
            connection,
            str(91000 + i),
            order_status=21,
            refund_status=3,
            pay_amount=36775,
            refund_amount=0,
            consign_time=0,
        )
        for i in range(2)
    ]
    settled = [
        historical(
            connection,
            str(92000 + i),
            order_status=22,
            confirm_time=int(NOW.replace(hour=10, minute=i).timestamp()),
            pay_amount=amount,
        )
        for i, amount in enumerate([41785, 20890])
    ]
    for row in refunds + settled:
        assert row.scope_status == PlatformOrder.Scope.HISTORICAL
        trade = Trade.objects.get(platform=row)
        assert trade.source == "API_CARRY"
    client.force_login(admin_user)
    with patch("app.workbench.views.timezone.now", return_value=NOW):
        dashboard = client.get(reverse("dashboard"))
    assert dashboard.context["refunds"] == 2
    assert dashboard.context["risk"] == 73550
    assert dashboard.context["completed_today_amount"] == 62675
    assert dashboard.context["completed_today_count"] == 2
    assert dashboard.context["today_due"] == 0
    assert dashboard.context["today_repayment_amount"] == 62675
    assert dashboard.context["today_repayment_count"] == 2
    assert "今日回款合计（已完成＋剩余预计）" in dashboard.content.decode()
    response = client.get(reverse("wb-refunds"))
    assert {t.platform_id for t in response.context["rows"]} == {r.pk for r in refunds}
    assert "买家已退货，等待卖家确认收货" in response.content.decode()
    day = NOW.date().isoformat()
    detail = client.get(
        reverse("wb-orders"),
        {
            "status": "COMPLETED",
            "date_field": "completed",
            "start": day,
            "end": day,
        },
    )
    assert {t.platform_id for t in detail.context["rows"]} == {r.pk for r in settled}
    assert sum(t.paid_fen for t in detail.context["rows"]) == 62675
    with patch("app.workbench.views.timezone.now", return_value=NOW):
        combined = client.get(reverse("wb-orders"), {"repayment": "today"})
    assert {t.platform_id for t in combined.context["rows"]} == {r.pk for r in settled}


def test_today_repayment_keeps_completed_amount_and_pending_details_in_sync(
    connection, admin_user, client
):
    completed = historical(
        connection, "92500", order_status=22, confirm_time=int(NOW.timestamp()), pay_amount=62675
    )
    pending = historical(
        connection,
        "92501",
        order_status=21,
        consign_time=int((NOW - timedelta(days=10)).timestamp()),
        pay_amount=10000,
    )
    historical(
        connection,
        "92502",
        order_status=21,
        consign_time=int((NOW - timedelta(days=9)).timestamp()),
        pay_amount=20000,
    )
    historical(connection, "92503", order_status=21, refund_status=3, pay_amount=30000)
    client.force_login(admin_user)
    with patch("app.workbench.views.timezone.now", return_value=NOW):
        dashboard = client.get(reverse("dashboard"))
        detail = client.get(reverse("wb-orders"), {"repayment": "today"})
    assert dashboard.context["today_repayment_amount"] == 72675
    assert dashboard.context["completed_today_amount"] == 62675
    assert dashboard.context["today_due"] == 10000
    assert {t.platform_id for t in detail.context["rows"]} == {completed.pk, pending.pk}
    assert sum(t.paid_fen for t in detail.context["rows"]) == 72675
    Trade.objects.filter(platform=pending).update(status="COMPLETED", completed_at=NOW)
    with patch("app.workbench.views.timezone.now", return_value=NOW):
        dashboard = client.get(reverse("dashboard"))
        detail = client.get(reverse("wb-orders"), {"repayment": "today"})
    assert dashboard.context["today_due"] == 0
    assert dashboard.context["completed_today_amount"] == 72675
    assert dashboard.context["today_repayment_amount"] == 72675
    assert dashboard.context["today_repayment_count"] == 2
    assert sum(t.paid_fen for t in detail.context["rows"]) == 72675


def test_refund_processing_recovery_and_completed_are_distinct(connection, admin_user, client):
    historical(connection, "93000", order_status=21, refund_status=3, refund_amount=0)
    for number in ("93001", "93002"):
        row = historical(
            connection,
            number,
            order_status=23,
            refund_status=5,
            refund_amount=20000,
            refund_time=int(NOW.timestamp()),
        )
        if number == "93002":
            Trade.objects.filter(platform=row).update(recovered_at=NOW)
    Trade.objects.filter(number__in=["93000", "93001", "93002"]).update(refund_type=2)
    client.force_login(admin_user)
    for params, numbers in [
        ({}, {"93000"}),
        ({"refund_view": "recovery"}, {"93000", "93001"}),
        ({"refund_view": "completed"}, {"93001", "93002"}),
        ({"history": "1"}, {"93000", "93001", "93002"}),
    ]:
        response = client.get(reverse("wb-refunds"), params)
        assert {t.number for t in response.context["rows"]} == numbers
        assert response.context["refund_counts"] == {
            "processing": 1,
            "recovery": 2,
            "completed": 2,
        }


def test_old_closed_history_stays_out_and_rebuild_preserves_carryover(connection):
    before = int((NOW.replace(day=1, hour=0) - timedelta(seconds=1)).timestamp())
    historical(connection, "94000", order_status=22, confirm_time=before)
    historical(
        connection,
        "94001",
        order_status=23,
        refund_status=5,
        refund_time=before,
        refund_amount=20000,
    )
    shipping = historical(connection, "94002", order_status=12)
    assert Trade.objects.get(platform=shipping).status == "SHIPPING"
    assert Trade.objects.count() == 1
    row = historical(
        connection, "94003", order_status=22, confirm_time=int(connection.sync_start_at.timestamp())
    )
    trade = Trade.objects.get(platform=row)
    trade.note = "保留人工备注"
    trade.unit_cost_fen = 8888
    trade.save()
    for _ in range(2):
        call_command("rebuild_workspace")
    trade.refresh_from_db()
    assert Trade.objects.count() == 2
    assert trade.note == "保留人工备注" and trade.unit_cost_fen == 8888
    assert connection.sync_start_at == Connection.objects.get(pk=connection.pk).sync_start_at


def test_missing_shipping_time_keeps_unshipped_money_without_guessing_date(
    connection, admin_user, client
):
    row = historical(connection, "95000", order_status=21, consign_time=0, confirm_time=0)
    trade = Trade.objects.get(platform=row)
    assert trade.status == "SHIPPING" and trade.guarantee_fen == 20000
    assert trade.reference_at is None and trade.issue == ""
    client.force_login(admin_user)
    with patch("app.workbench.views.timezone.now", return_value=NOW):
        response = client.get(reverse("dashboard"))
    assert response.context["unknown_due_count"] == 0
    assert response.context["unknown_due_amount"] == 0
    assert response.context["today_due"] == response.context["soon_due"] == 0
    detail = client.get(reverse("wb-pending"), {"due": "unknown"})
    assert list(detail.context["rows"]) == []
    assert (
        classify(payload(order_status=21, confirm_time=int(NOW.timestamp()), consign_time=0))[0]
        == "REVIEW"
    )


def test_completed_today_uses_beijing_day_not_payment_or_expected_day(
    connection, admin_user, client
):
    midnight = NOW.replace(hour=0, minute=0, second=0)
    for i, completed in enumerate(
        [midnight - timedelta(seconds=1), midnight, midnight + timedelta(days=1)]
    ):
        historical(
            connection, str(96000 + i), order_status=22, confirm_time=int(completed.timestamp())
        )
    client.force_login(admin_user)
    with patch("app.workbench.views.timezone.now", return_value=NOW):
        response = client.get(reverse("dashboard"))
    assert response.context["completed_today_count"] == 1
    assert response.context["completed_today_amount"] == 20000
