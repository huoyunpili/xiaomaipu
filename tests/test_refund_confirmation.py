import pytest
from django.urls import reverse

from app.common.business import BusinessError
from app.integrations.services import store_order
from app.workbench.models import Trade
from app.workbench.services import confirm_refund_success
from tests.test_workspace import connection as workspace_connection
from tests.test_workspace import make_trade, payload

connection = workspace_connection
pytestmark = pytest.mark.django_db


def test_confirmed_success_survives_sync_without_guessing_amount_or_supplier_recovery(
    connection, admin_user, client
):
    data = payload(order_status=23, refund_status=0, refund_amount=0, refund_time=1789298763)
    row = store_order(connection, data)
    trade = Trade.objects.get(platform=row)
    assert trade.status == "REVIEW"
    confirm_refund_success(trade, admin_user)
    store_order(connection, {**data, "update_time": data["update_time"] + 1})
    trade.refresh_from_db()
    assert trade.status == "REFUNDED" and trade.issue == ""
    assert trade.refunded_fen is None
    assert trade.recovered_at is None
    assert trade.guarantee_fen == 0 and trade.profit_fen is None
    assert trade.platform.snapshot["refund_amount"] == 0
    client.force_login(admin_user)
    html = client.get(reverse("wb-detail", args=[trade.pk])).content.decode()
    assert "平台未提供" not in html
    assert "已退款金额：" not in html
    other = store_order(connection, {**data, "order_no": "987654321"})
    assert Trade.objects.get(platform=other).status == "REVIEW"
    store_order(
        connection,
        {
            **data,
            "refund_status": 5,
            "refund_amount": data["pay_amount"],
            "update_time": data["update_time"] + 2,
        },
    )
    trade.refresh_from_db()
    assert trade.refunded_fen == data["pay_amount"]


def test_cannot_confirm_refund_for_unshipped_order(connection, admin_user):
    trade = make_trade(connection)
    with pytest.raises(BusinessError):
        confirm_refund_success(trade, admin_user)
