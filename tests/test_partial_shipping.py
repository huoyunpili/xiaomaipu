import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import close_old_connections

from app.common.business import BusinessError
from app.inventory.models import InventoryBalance, StockMovement
from app.orders.models import Reservation, Shipment
from app.orders.returns import receive_return
from app.orders.services import order_action
from app.procurement.allocation import reserve_purchase_receipt
from app.procurement.models import Purchase
from app.procurement.services import create_purchase
from tests.test_business import action, draft, goods, money
from tests.test_procurement import operate
from tests.test_shortage_procurement import purchase_data, reserve_data, shortage

pytestmark = pytest.mark.django_db


def test_cancel_remaining_preserves_shipped_and_refund_is_separate(admin_user, shop):
    sku, lot = goods(admin_user, quantity=3)
    order = draft(admin_user, sku, quantity=3, lot=lot)
    action(admin_user, order, "confirm")
    money(admin_user, order, "RECEIPT", 60000)
    action(admin_user, order, "ship", quantity=1, delivery_method="HANDOVER")
    action(admin_user, order, "cancel_remaining", reduction_fen=40000, reason="剩余两件不要了")
    assert order.items.get().cancelled_qty == 2
    assert order.items.get().shortage_qty == 0
    assert order.net_received_fen == 60000 and order.adjusted_due_fen == 20000
    assert order.realized_profit_fen is None
    assert InventoryBalance.objects.get().available_qty == 2
    money(admin_user, order, "REFUND", 40000)
    assert order.realized_profit_fen == 9500
    with pytest.raises(BusinessError):
        action(admin_user, order, "cancel_remaining", reduction_fen=0, reason="重复关闭")


def test_split_reservation_shipping_cost_returns_and_profit(admin_user, shop):
    sku, lot = goods(admin_user, quantity=3)
    order = draft(admin_user, sku, quantity=3, lot=lot)
    action(admin_user, order, "confirm")
    money(admin_user, order, "RECEIPT", 60000)
    action(
        admin_user,
        order,
        "ship",
        quantity=1,
        delivery_method="EXPRESS",
        carrier="测试快递",
        tracking_no="FIRST",
        fulfillment_fee_fen=500,
    )
    assert order.status == "PARTIAL" and order.realized_profit_fen is None
    assert order.cost_fen == 10500
    assert InventoryBalance.objects.get().reserved_qty == 2
    assert Reservation.objects.get(status="ACTIVE").quantity == 2
    shipped = Reservation.objects.get(status="CONSUMED")
    with pytest.raises(BusinessError):
        receive_return(
            actor=admin_user,
            submission_key=uuid.uuid4(),
            reservation_id=shipped.pk,
            quantity=2,
            reason="不能退未发出的货",
        )
    with pytest.raises(BusinessError):
        action(admin_user, order, "cancel", reason="不能取消已发出部分")
    with pytest.raises(BusinessError):
        action(admin_user, order, "complete")
    action(
        admin_user, order, "ship", quantity=2, delivery_method="HANDOVER", fulfillment_fee_fen=200
    )
    assert order.status == "SHIPPED"  # first express parcel still needs confirmation
    assert order.cost_fen == 31500 and order.fees_fen == 700
    assert order.realized_profit_fen is None
    assert Shipment.objects.count() == 2
    assert Shipment.objects.get(tracking_no="FIRST").quantity == 1
    assert Shipment.objects.get(tracking_no="FIRST").cost_fen == 10500
    action(admin_user, order, "complete")
    assert order.realized_profit_fen == 27800
    assert not Shipment.objects.filter(completed=False).exists()
    assert InventoryBalance.objects.get().on_hand_qty == 0


def test_ship_available_then_purchase_and_complete(admin_user, shop):
    order, supplier = shortage(admin_user)
    action(admin_user, order, "ship", quantity=1, delivery_method="HANDOVER")
    assert order.status == "PARTIAL" and order.items.get().shortage_qty == 2
    result = create_purchase(**purchase_data(admin_user, order, supplier))
    purchase = Purchase.objects.get(pk=result["purchase_id"])
    operate(admin_user, purchase, "order")
    operate(admin_user, purchase, "receive", quantity=2)
    reserve_purchase_receipt(**reserve_data(admin_user, order, purchase.receipts.get()))
    action(admin_user, order, "confirm")
    assert order.status == "PARTIAL"
    action(admin_user, order, "ship", quantity=2, delivery_method="HANDOVER")
    assert order.status == "COMPLETED"
    money(admin_user, order, "RECEIPT", 60000)
    assert order.realized_profit_fen == 33500


@pytest.mark.django_db(transaction=True)
def test_duplicate_and_concurrent_shipment(admin_user, shop):
    sku, lot = goods(admin_user, quantity=2)
    order = draft(admin_user, sku, quantity=2, lot=lot)
    action(admin_user, order, "confirm")
    data = dict(
        actor=admin_user,
        submission_key=uuid.uuid4(),
        order_id=order.pk,
        version=order.version,
        action_name="ship",
        quantity=1,
        delivery_method="HANDOVER",
    )

    def ship(_):
        close_old_connections()
        try:
            return order_action(**data)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(ship, range(2)))
    assert results[0] == results[1]
    assert Shipment.objects.count() == 1
    assert StockMovement.objects.filter(kind="SALE_SHIPMENT").count() == 1
    assert InventoryBalance.objects.get().on_hand_qty == 1
    with pytest.raises(BusinessError):
        action(admin_user, order, "ship", quantity=2, delivery_method="HANDOVER")
    assert Shipment.objects.count() == 1
