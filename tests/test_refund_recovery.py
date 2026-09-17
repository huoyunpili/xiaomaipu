import pytest
from django.urls import reverse
from django.utils import timezone

from app.integrations.services import store_order
from app.workbench.models import Trade
from tests.test_workspace import connection as workspace_connection
from tests.test_workspace import make_trade, payload

connection = workspace_connection
pytestmark = pytest.mark.django_db


def test_recovery_summary_only_counts_unrecovered_returns(connection, admin_user, client):
    for index, (status, refund_type, recovered) in enumerate(
        [
            ("REFUNDING", 2, False),
            ("REFUNDED", 2, False),
            ("REFUNDED", 1, False),
            ("REFUNDED", None, False),
            ("REFUNDED", 2, True),
        ]
    ):
        trade = make_trade(connection, number=str(80000 + index))
        Trade.objects.filter(pk=trade.pk).update(
            status=status,
            refund_type=refund_type,
            quantity=1,
            unit_cost_fen=32000,
            recovered_at=timezone.now() if recovered else None,
        )
    client.force_login(admin_user)
    dashboard = client.get(reverse("dashboard"))
    assert dashboard.context["recovery"] == 64000
    detail = client.get(reverse("wb-refunds"), {"refund_view": "recovery"})
    assert {t.number for t in detail.context["rows"]} == {"80000", "80001"}
    assert sum(t.cost_fen for t in detail.context["rows"]) == 64000
    Trade.objects.filter(number="80000").update(recovered_at=timezone.now())
    assert client.get(reverse("dashboard")).context["recovery"] == 32000


def test_pending_refund_recovery_is_manual_colored_and_survives_sync(
    connection, admin_user, client
):
    data = payload(order_status=21, refund_status=3)
    row = store_order(connection, data)
    trade = Trade.objects.get(platform=row)
    url = reverse("wb-detail", args=[trade.pk])
    client.force_login(admin_user)
    for page in (url, reverse("wb-refunds"), reverse("wb-orders")):
        html = client.get(page).content.decode()
        assert "wb-recovery-unrecovered" in html
        assert "供应商货款：未追回" in html
        assert "平台未提供" not in html
        assert "暂停发货和回款参考" not in html
        assert "回款时间暂停参考" not in html
    response = client.post(url, {"recovered": "on"})
    assert response.status_code == 302
    trade.refresh_from_db()
    recovered_at = trade.recovered_at
    assert recovered_at is not None
    assert trade.status == "REFUNDING"
    assert trade.guarantee_fen == data["pay_amount"]
    assert trade.refunded_at is None
    for page in (url, reverse("wb-refunds"), reverse("wb-orders")):
        html = client.get(page).content.decode()
        assert "wb-recovery-recovered" in html
        assert "供应商货款：已追回" in html
        assert "wb-recovery-unrecovered" not in html
    store_order(connection, {**data, "update_time": data["update_time"] + 1})
    trade.refresh_from_db()
    assert trade.recovered_at == recovered_at
    store_order(
        connection,
        {
            **data,
            "update_time": data["update_time"] + 2,
            "order_status": 23,
            "refund_status": 5,
            "refund_time": data["update_time"] + 2,
            "refund_amount": data["pay_amount"],
        },
    )
    trade.refresh_from_db()
    assert trade.status == "REFUNDED"
    assert trade.recovered_at == recovered_at
    assert trade.recovery_fen == 0


def test_pending_refund_recovery_can_be_corrected_to_unrecovered(connection, admin_user, client):
    trade = make_trade(connection, order_status=21, refund_status=3)
    client.force_login(admin_user)
    url = reverse("wb-detail", args=[trade.pk])
    assert client.post(url, {"recovered": "on"}).status_code == 302
    assert client.post(url, {"recovered": ""}).status_code == 302
    trade.refresh_from_db()
    assert trade.recovered_at is None
    assert trade.status == "REFUNDING"
    assert "wb-recovery-unrecovered" in client.get(url).content.decode()


def test_non_refund_order_cannot_be_marked_recovered(connection, admin_user, client):
    trade = make_trade(connection)
    client.force_login(admin_user)
    response = client.post(reverse("wb-detail", args=[trade.pk]), {"recovered": "on"})
    assert response.status_code == 200
    assert "recovered" in response.context["form"].errors
    trade.refresh_from_db()
    assert trade.recovered_at is None


def test_inline_recovery_only_updates_recovery_and_is_repeatable(connection, admin_user, client):
    trade = make_trade(connection, order_status=21, refund_status=3)
    trade.note = "保留备注"
    trade.supplier = "原供应商"
    trade.unit_cost_fen = 32000
    trade.save()
    client.force_login(admin_user)
    url = reverse("wb-recovery", args=[trade.pk])
    assert client.get(url).status_code == 405
    assert client.post(url, {"recovered": "invalid"}).status_code == 400
    response = client.post(url, {"recovered": "yes"})
    assert response.status_code == 200 and response.json()["recovered"] is True
    trade.refresh_from_db()
    confirmed_at = trade.recovered_at
    assert confirmed_at is not None
    assert trade.note == "保留备注" and trade.supplier == "原供应商"
    assert trade.unit_cost_fen == 32000 and trade.status == "REFUNDING"
    client.post(url, {"recovered": "yes"})
    trade.refresh_from_db()
    assert trade.recovered_at == confirmed_at
    response = client.post(url, {"recovered": "no"})
    assert response.json()["recovered"] is False
    trade.refresh_from_db()
    assert trade.recovered_at is None
    html = client.get(reverse("wb-refunds")).content.decode()
    assert f'data-recovery-url="{url}"' in html
    assert 'type="button" data-save-recovery' in html
    assert "登记 / 修改追回状态" not in html


def test_inline_recovery_checks_status_login_and_csrf(connection, admin_user, client):
    from django.test import Client

    trade = make_trade(connection)
    url = reverse("wb-recovery", args=[trade.pk])
    assert client.post(url, {"recovered": "yes"}).status_code == 302
    client.force_login(admin_user)
    assert client.post(url, {"recovered": "yes"}).status_code == 409
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(admin_user)
    assert csrf_client.post(url, {"recovered": "yes"}).status_code == 403
    trade.refresh_from_db()
    assert trade.recovered_at is None
