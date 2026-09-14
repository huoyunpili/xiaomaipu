import uuid

import pytest

from app.common.dashboard import financial_summary, goods_summary
from app.finance.services import record_customer_payment
from tests.test_business import action, draft, goods, money
from tests.test_procurement import operate, setup_purchase

pytestmark = pytest.mark.django_db


def test_dashboard_separates_cash_platform_payment_goods_and_supplier_balance(
    client, admin_user, shop
):
    sku, lot = goods(admin_user, quantity=3, cost=10000)
    order = draft(admin_user, sku, lot=lot, channel="XIANYU", price=20000)
    action(admin_user, order, "confirm", reason="已核对买家付款")
    money(admin_user, order, "RECEIPT", 5000)
    record_customer_payment(
        actor=admin_user,
        submission_key=uuid.uuid4(),
        order_id=order.pk,
        version=order.version,
        amount_fen=20000,
        source_ref="dashboard-payment",
        evidence="平台付款凭据",
    )

    purchase = setup_purchase(admin_user, quantity=2)
    operate(admin_user, purchase, "order")
    operate(admin_user, purchase, "pay", amount_fen=5000)
    operate(admin_user, purchase, "ship", quantity=2)

    assert financial_summary() == {
        "cash_received_fen": 5000,
        "customer_payment_fen": 20000,
        "receivable_fen": 15000,
        "realized_profit_fen": 0,
        "supplier_paid_fen": 5000,
        "supplier_payable_fen": 16000,
        "supplier_refund_fen": 0,
        "platform_pending_fen": 0,
    }
    assert goods_summary() == {
        "on_hand_qty": 3,
        "available_qty": 2,
        "reserved_qty": 1,
        "inspection_qty": 0,
        "in_transit_qty": 2,
        "pending_customer_qty": 1,
        "stock_cost_fen": 31500,
        "platform_pending_qty": 0,
    }

    client.force_login(admin_user)
    page = client.get("/").content.decode()
    assert "钱从客户流入，再向供应商流出" in page
    assert "客户已付，不等同现金到账" in page
    assert "平台订单待核对金额" in page
    assert "货从供应商流入，再向客户流出" in page
    assert "K1" in page and "K7" in page and "售后钱货不同步" in page
