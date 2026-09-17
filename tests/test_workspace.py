import csv
import io
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse
from django.utils import timezone

from app.common.business import BusinessError
from app.integrations.models import Connection
from app.integrations.services import store_order
from app.workbench.importing import HEADERS, import_rows
from app.workbench.models import Trade
from app.workbench.services import classify, create_batches, update_product

pytestmark = pytest.mark.django_db


@pytest.fixture
def connection(shop, admin_user):
    return Connection.objects.create(
        shop=shop,
        actor=admin_user,
        seller_id="123",
        sync_start_at=timezone.now() - timedelta(days=90),
    )


def payload(number="123456789", **kwargs):
    now = int(timezone.now().timestamp())
    return {
        "order_no": number,
        "order_status": 12,
        "order_time": now - 100,
        "pay_time": now,
        "pay_amount": 20000,
        "update_time": now,
        "refund_status": 0,
        "goods": {
            "product_id": "42",
            "sku_id": "7",
            "title": "测试商品",
            "sku_text": "黑色 大号",
            "quantity": 2,
            "images": ["https://example.com/a.jpg"],
        },
        "receiver_name": "测试客户",
        "receiver_mobile": "13000000000",
        "prov_name": "测试省",
        "address": "测试路123号",
        **kwargs,
    }


def make_trade(connection, **kwargs):
    row = store_order(connection, payload(**kwargs))
    return Trade.objects.get(platform=row)


def test_projection_cost_refund_completion(connection, admin_user):
    t = make_trade(connection)
    assert t.status == "SHIPPING" and t.guarantee_fen == 20000
    assert t.profit_fen is None and t.image.endswith("a.jpg")
    assert t.receiver == "测试客户"
    assert "receiver_name" not in t.platform.snapshot
    update_product(t.product, 6000, "供应商甲", admin_user)
    t.refresh_from_db()
    assert t.cost_fen == 12000 and t.fee_fen == 320 and t.profit_fen == 7680
    update_product(t.product, 8000, "供应商甲", admin_user)
    t.refresh_from_db()
    assert t.cost_fen == 12000
    now = int(timezone.now().timestamp())
    t = make_trade(connection, order_status=22, confirm_time=now, update_time=now + 1)
    assert t.guarantee_fen == 0 and t.profit_fen == 7680
    t = make_trade(
        connection,
        order_status=22,
        refund_status=5,
        refund_time=now,
        refund_amount=20000,
        update_time=now + 2,
    )
    assert t.status == "REFUNDED" and t.guarantee_fen == 0 and t.fee_fen == 0
    assert t.profit_fen is None and t.loss_fen == 0 and t.recovery_fen == 12000


def test_refund_application_and_unknown():
    assert classify(payload(refund_status=1))[0] == "REFUNDING"
    assert classify(payload(order_status=999))[0] == "REVIEW"
    assert classify(payload(refund_time=1700000000, refund_amount=100))[0] == "REVIEW"
    assert classify(payload(pay_time=0))[0] == "REVIEW"


def test_historical_scope_not_auto_added(connection):
    store_order(connection, payload(order_time=1, order_status=22, confirm_time=2))
    assert Trade.objects.count() == 0


def test_export_snapshot_stale_and_validation(connection, admin_user):
    t = make_trade(connection)
    with pytest.raises(BusinessError):
        create_batches([t], "shipping", admin_user)
    update_product(t.product, 6000, "供应商甲", admin_user)
    t.refresh_from_db()
    b = create_batches([t], "shipping", admin_user)[0]
    assert b.snapshot[0]["title"] == "测试商品"
    assert b.snapshot[0]["spec"] == "黑色 大号"
    now = int(timezone.now().timestamp())
    make_trade(connection, refund_status=1, update_time=now + 5)
    b.refresh_from_db()
    assert b.stale and b.snapshot[0]["phone"] == "13000000000"
    assert b.snapshot[0]["status"] == "待发货"


def test_shipping_ignores_retired_filters(connection, admin_user, client):
    old = timezone.now() - timedelta(days=5)
    trade = make_trade(connection, pay_time=int(old.timestamp()))
    client.force_login(admin_user)
    response = client.get(
        reverse("wb-shipping"),
        {"q": "no-match", "supplier": "no-match", "start": "2000-01-01", "end": "2000-01-02"},
    )
    assert response.status_code == 200
    assert trade in response.context["rows"]
    assert response.context["query"] == ""
    assert b'form class="wb-filters"' not in response.content
    assert (
        b'name="export_supplier"' in response.content
        or b'name="supplier_filter"' in response.content
    )
    assert b'form class="wb-filters"' in client.get(reverse("wb-orders")).content


def test_all_pages_and_profits(connection, admin_user, client):
    now = timezone.now()
    t = make_trade(connection, order_status=22, confirm_time=int(now.timestamp()))
    update_product(t.product, 6000, "甲", admin_user)
    client.force_login(admin_user)
    for name in (
        "dashboard",
        "wb-orders",
        "wb-shipping",
        "wb-pending",
        "wb-refunds",
        "wb-settings",
        "wb-costs",
        "wb-history",
        "wb-profits",
    ):
        response = client.get(reverse(name))
        assert response.status_code == 200, name
    response = client.get(reverse("wb-profits"), {"mode": "actual", "range": "today"})
    assert response.context["summary"]["profit"] == 7680
    assert b"76.80" in response.content
    bad = client.get(
        reverse("wb-profits"), {"range": "custom", "start": "2026-09-15", "end": "2026-09-01"}
    )
    assert bad.status_code == 400
    assert client.get(reverse("wb-detail", args=[t.pk])).status_code == 200


def test_date_boundaries_and_loss_separate(connection, admin_user, client):
    t = make_trade(connection, order_status=22, confirm_time=int(timezone.now().timestamp()))
    update_product(t.product, 6000, "甲", admin_user)
    t.refresh_from_db()
    t.paid_at = timezone.now() - timedelta(days=30)
    t.loss_fen = 1200
    t.loss_at = timezone.now()
    t.save()
    client.force_login(admin_user)
    actual = client.get(reverse("wb-profits"), {"mode": "actual", "range": "today"})
    expected = client.get(reverse("wb-profits"), {"mode": "expected", "range": "today"})
    assert actual.context["summary"]["profit"] == 7680
    assert actual.context["loss_total"] == 1200
    assert expected.context["summary"]["profit"] == 0


def test_csv_import_idempotent_and_atomic(shop, admin_user):
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(HEADERS)
    writer.writerow(
        [
            "999",
            "42",
            "商品",
            "黑色",
            "2",
            "200",
            "已完成",
            "2026-01-01 10:00",
            "2026-01-01 10:01",
            "2026-01-01 12:00",
            "2026-01-11 12:00",
            "",
            "客户",
            "13000000000",
            "地址",
            "甲",
        ]
    )
    content = stream.getvalue()
    assert import_rows(content, admin_user) == (1, 0)
    assert import_rows(content, admin_user) == (0, 1)
    assert Trade.objects.count() == 1
    with pytest.raises(BusinessError):
        import_rows(content.replace("999", "998") + "bad,row\n", admin_user)
    assert Trade.objects.count() == 1


def test_permissions_and_refund_record(connection, admin_user, client):
    t = make_trade(connection)
    assert client.get(reverse("wb-profits")).status_code == 302
    client.force_login(admin_user)
    response = client.post(reverse("wb-detail", args=[t.pk]), {"recovered": "on"})
    assert response.status_code == 200
    t.refresh_from_db()
    assert t.recovered_at is None
    response = client.post(reverse("wb-detail", args=[t.pk]), {"correction": "100"})
    assert response.status_code == 200
    t.refresh_from_db()
    assert t.unit_cost_fen is None


def test_guarantee_drilldown_contains_unshipped_and_refund_risk(connection, client, admin_user):
    make_trade(connection)
    make_trade(connection, number="123456790", refund_status=1)
    make_trade(
        connection,
        number="123456791",
        order_status=22,
        confirm_time=int(timezone.now().timestamp()),
    )
    client.force_login(admin_user)
    dashboard = client.get(reverse("dashboard"))
    assert f"{reverse('wb-orders')}?guarantee=1" in dashboard.content.decode()
    detail = client.get(reverse("wb-orders"), {"guarantee": "1"})
    rows = list(detail.context["rows"])
    assert len(rows) == dashboard.context["guarantee_count"] == 2
    assert sum(t.guarantee_fen for t in rows) == dashboard.context["guarantee"] == 40000


def test_profit_export_preserves_saved_range(connection, client, admin_user):
    t = make_trade(connection)
    update_product(t.product, 6000, "甲", admin_user)
    client.force_login(admin_user)
    response = client.get(reverse("wb-profits"), {"mode": "expected", "range": "today"})
    assert response.context["summary"]["profit"] == 7680
    refreshed = client.get(reverse("wb-profits"))
    assert refreshed.context["mode"] == "expected"
    exported = client.get(reverse("wb-profits"), {"export": "csv"})
    assert t.number in exported.content.decode("utf-8-sig")
    assert "7680" in exported.content.decode("utf-8-sig")


def test_profit_sort_and_export_share_totals_and_missing_costs(connection, client, admin_user):
    first = make_trade(connection)
    update_product(first.product, 6000, "甲", admin_user)
    second = make_trade(connection, number="123456790", pay_amount=10000)
    second.title = "另一个商品"
    second.save()
    missing = make_trade(connection, number="123456791")
    Trade.objects.filter(pk=missing.pk).update(unit_cost_fen=None)
    client.force_login(admin_user)
    params = {"mode": "expected", "range": "today", "sort": "profit_low"}
    response = client.get(reverse("wb-profits"), params)
    assert [t.pk for t in response.context["rows"]] == [second.pk, first.pk, missing.pk]
    summary = response.context["summary"]
    assert (summary["profit"], summary["sales"], summary["missing_sales"]) == (5520, 30000, 20000)
    assert sum(p["profit"] for p in response.context["trend_chart"]) == summary["profit"]
    exported = client.get(reverse("wb-profits"), {**params, "export": "csv"})
    data = list(csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig"))))
    included = [r for r in data if r["计算口径"] == "纳入利润合计"]
    total = next(r for r in data if r["订单号"] == "合计（已知成本）")
    assert sum(int(r["利润分"]) for r in included) == int(total["利润分"]) == summary["profit"]
    assert sum(int(r["实付分"]) for r in included) == int(total["实付分"]) == summary["sales"]
    assert data[0]["订单号"] == second.number
    assert data[0]["统计时间"].endswith("+08:00")


def test_beijing_date_edges_and_separate_refund_loss(connection, client, admin_user):
    zone = ZoneInfo("Asia/Shanghai")
    first = make_trade(connection)
    update_product(first.product, 6000, "甲", admin_user)
    for number, completed in (
        ("200000001", datetime(2026, 9, 1, tzinfo=zone)),
        ("200000002", datetime(2026, 9, 30, 23, 59, 59, tzinfo=zone)),
        ("200000003", datetime(2026, 10, 1, tzinfo=zone)),
        ("200000004", datetime(2026, 8, 31, 23, 59, 59, tzinfo=zone)),
    ):
        make_trade(
            connection, number=number, order_status=22, confirm_time=int(completed.timestamp())
        )
    loss = make_trade(
        connection,
        number="200000005",
        refund_status=5,
        refund_amount=20000,
        refund_time=int(timezone.now().timestamp()),
    )
    Trade.objects.filter(pk=loss.pk).update(
        loss_fen=1200, loss_at=datetime(2026, 9, 15, tzinfo=zone)
    )
    client.force_login(admin_user)
    params = {"mode": "actual", "range": "custom", "start": "2026-09-01", "end": "2026-09-30"}
    response = client.get(reverse("wb-profits"), params)
    assert [t.number for t in response.context["rows"]] == ["200000001", "200000002"]
    assert response.context["summary"]["profit"] == 15360
    assert response.context["loss_total"] == 1200
    exported = client.get(reverse("wb-profits"), {**params, "export": "csv"})
    data = list(csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig"))))
    refund_loss = next(r for r in data if r["状态"] == "售后损失（单列）")
    assert refund_loss["利润分"] == "" and refund_loss["运费损失分"] == "1200"


def test_export_rechecks_database_state_and_lists_changed_order(connection, admin_user, client):
    t = make_trade(connection)
    update_product(t.product, 6000, "甲", admin_user)
    t.refresh_from_db()
    b = create_batches([t], "shipping", admin_user)[0]
    Trade.objects.filter(pk=t.pk).update(status="REFUNDING")
    with pytest.raises(BusinessError):
        create_batches([t], "shipping", admin_user)
    client.force_login(admin_user)
    response = client.get(reverse("wb-batch", args=[b.pk]))
    assert [r["number"] for r in response.context["changed"]] == [t.number]
    b.refresh_from_db()
    assert b.snapshot[0]["status"] == "待发货"


def test_cost_versions_follow_payment_time_and_never_freeze_unpaid(connection, admin_user):
    now = timezone.now()
    unpaid = make_trade(connection, order_status=11, pay_time=0)
    with patch("app.workbench.services.timezone.now", return_value=now - timedelta(days=2)):
        update_product(unpaid.product, 6000, "甲", admin_user)
    unpaid.refresh_from_db()
    assert unpaid.unit_cost_fen is None
    with patch("app.workbench.services.timezone.now", return_value=now - timedelta(days=1)):
        update_product(unpaid.product, 8000, "甲", admin_user)
    old_payment = make_trade(
        connection,
        number="200000001",
        pay_time=int((now - timedelta(days=1, hours=12)).timestamp()),
    )
    new_payment = make_trade(connection, number="200000002", pay_time=int(now.timestamp()))
    assert old_payment.unit_cost_fen == 6000
    assert new_payment.unit_cost_fen == 8000
    assert list(unpaid.product.revisions.values_list("unit_fen", flat=True)) == [6000, 8000]
    paid = make_trade(
        connection, pay_time=int(now.timestamp()), update_time=int(now.timestamp()) + 1
    )
    assert paid.unit_cost_fen == 8000
    update_product(unpaid.product, 9000, "甲", admin_user)
    old_payment.refresh_from_db()
    paid.refresh_from_db()
    assert old_payment.unit_cost_fen == 6000 and paid.unit_cost_fen == 8000


def test_all_orders_sort_oldest_pending_and_newest_ended(connection, admin_user, client):
    older = make_trade(
        connection,
        number="200000001",
        pay_time=int((timezone.now() - timedelta(days=2)).timestamp()),
    )
    newer = make_trade(connection, number="200000002")
    ended_old = make_trade(
        connection,
        number="200000003",
        order_status=22,
        confirm_time=int(timezone.now().timestamp()),
    )
    ended_new = make_trade(
        connection,
        number="200000004",
        order_status=22,
        confirm_time=int(timezone.now().timestamp()),
    )
    Trade.objects.filter(pk=ended_old.pk).update(
        status_changed_at=timezone.now() - timedelta(days=1)
    )
    client.force_login(admin_user)
    response = client.get(reverse("wb-orders"))
    assert [t.pk for t in response.context["rows"]] == [
        older.pk,
        newer.pk,
        ended_new.pk,
        ended_old.pk,
    ]
