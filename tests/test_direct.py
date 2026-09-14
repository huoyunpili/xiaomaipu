import uuid

import pytest

from app.common.business import BusinessError
from app.inventory.models import InventoryBalance, StockMovement
from app.orders.models import Reservation
from app.orders.returns import inspect_return, receive_return
from app.procurement.direct import dispatch_direct
from app.procurement.models import Purchase
from app.procurement.services import create_purchase
from tests.test_business import action, money
from tests.test_procurement import operate
from tests.test_shortage_procurement import purchase_data, shortage

pytestmark = pytest.mark.django_db


def test_direct_delivery_has_no_stock_movement_and_return_can_restock(admin_user, shop):
    order, supplier = shortage(admin_user)
    result = create_purchase(**purchase_data(admin_user, order, supplier), direct=True)
    purchase = Purchase.objects.get(pk=result["purchase_id"])
    operate(admin_user, purchase, "order")
    with pytest.raises(BusinessError):
        operate(admin_user, purchase, "receive", quantity=2)
    order.refresh_from_db()
    before = StockMovement.objects.count()
    data = dict(
        actor=admin_user,
        submission_key=uuid.uuid4(),
        purchase_id=purchase.pk,
        version=purchase.version,
        order_version=order.version,
        quantity=2,
        carrier="测试快递",
        tracking_no="DIRECT1",
        evidence_note="供应商直发未拍摄",
        acknowledged=True,
    )
    assert dispatch_direct(**data) == dispatch_direct(**data)
    assert StockMovement.objects.count() == before
    assert InventoryBalance.objects.get().on_hand_qty == 1
    purchase.refresh_from_db()
    assert purchase.direct_qty == 2 and purchase.pending_qty == 0
    action(admin_user, order, "ship", quantity=1, delivery_method="HANDOVER")
    action(admin_user, order, "complete")
    money(admin_user, order, "RECEIPT", 60000)
    assert order.realized_profit_fen == 33500
    reservation = Reservation.objects.get(lot__isnull=True)
    returned = receive_return(
        actor=admin_user,
        submission_key=uuid.uuid4(),
        reservation_id=reservation.pk,
        quantity=1,
        reason="实际收到直发退货",
    )
    assert InventoryBalance.objects.get().inspection_qty == 1
    inspect_return(
        actor=admin_user,
        submission_key=uuid.uuid4(),
        return_id=returned["return_id"],
        result="RESTOCKED",
        reason="验收正常",
    )
    assert InventoryBalance.objects.get().available_qty == 1
    order.refresh_from_db()
    assert order.recovered_cost_fen == 8000
