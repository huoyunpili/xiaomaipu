import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import close_old_connections

from app.common.business import BusinessError
from app.inventory.models import InventoryBalance, StockLot
from app.procurement.allocation import reserve_purchase_receipt
from app.procurement.models import Purchase
from app.procurement.services import create_purchase, save_supplier
from tests.test_business import action, draft, goods, money
from tests.test_procurement import operate

pytestmark = pytest.mark.django_db


def shortage(actor):
    sku, lot = goods(actor, quantity=1, description="99 新")
    order = draft(actor, sku, quantity=3, lot=lot)
    action(actor, order, "confirm")
    supplier = save_supplier(actor=actor, submission_key=uuid.uuid4(), name="补货来源")
    return order, supplier["supplier_id"]


def purchase_data(actor, order, supplier):
    return dict(
        actor=actor,
        submission_key=uuid.uuid4(),
        sku_id=order.items.get().sku_id,
        order_item_id=order.items.get().pk,
        supplier_id=supplier,
        quantity=2,
        unit_cost_fen=8000,
    )


def reserve_data(actor, order, receipt):
    order.refresh_from_db()
    receipt.lot.refresh_from_db()
    return dict(
        actor=actor,
        submission_key=uuid.uuid4(),
        receipt_id=receipt.pk,
        version=order.version,
        lot_version=receipt.lot.version,
        quantity=2,
        acknowledged=True,
    )


def test_shortage_purchase_actual_condition_and_historical_profit(admin_user, shop):
    order, supplier = shortage(admin_user)
    data = purchase_data(admin_user, order, supplier)
    result = create_purchase(**data)
    assert create_purchase(**data) == result
    with pytest.raises(BusinessError):
        create_purchase(**{**data, "submission_key": uuid.uuid4()})
    purchase = Purchase.objects.get(pk=result["purchase_id"])
    operate(admin_user, purchase, "order")
    operate(
        admin_user,
        purchase,
        "receive",
        quantity=2,
        condition_description="轻微漏光，无配件",
        condition_label="95 新",
    )
    receipt = purchase.receipts.get()
    with pytest.raises(BusinessError):
        create_purchase(**{**data, "submission_key": uuid.uuid4()})
    data = reserve_data(admin_user, order, receipt)
    with pytest.raises(BusinessError):
        reserve_purchase_receipt(**{**data, "acknowledged": False})
    assert order.items.get().shortage_qty == 2
    assert reserve_purchase_receipt(**data) == reserve_purchase_receipt(**data)
    assert order.items.get().condition_snapshot["condition_description"] == "99 新"
    assert order.items.get().shortage_qty == 0
    assert (
        order.items.get().reservations.get(lot=receipt.lot).condition_snapshot["condition_label"]
        == "95 新"
    )
    with pytest.raises(BusinessError):
        operate(admin_user, purchase, "return", quantity=1, receipt_id=receipt.pk)
    action(admin_user, order, "ship", delivery_method="HANDOVER")
    money(admin_user, order, "RECEIPT", 60000)
    assert order.cost_fen == 26500 and order.realized_profit_fen == 33500
    assert InventoryBalance.objects.get().on_hand_qty == 0


def test_cancelled_order_keeps_purchase_and_rejects_allocation(admin_user, shop, client):
    order, supplier = shortage(admin_user)
    result = create_purchase(**purchase_data(admin_user, order, supplier))
    purchase = Purchase.objects.get(pk=result["purchase_id"])
    operate(admin_user, purchase, "order")
    action(admin_user, order, "cancel", reason="客户取消")
    operate(admin_user, purchase, "receive", quantity=2)
    with pytest.raises(BusinessError):
        reserve_purchase_receipt(**reserve_data(admin_user, order, purchase.receipts.get()))
    assert InventoryBalance.objects.get().available_qty == 3
    assert not purchase.closed
    client.force_login(admin_user)
    response = client.get(f"/purchases/{purchase.pk}/")
    assert "关联订单已取消" in response.content.decode()
    with pytest.raises(BusinessError):
        create_purchase(**purchase_data(admin_user, order, supplier))


@pytest.mark.django_db(transaction=True)
def test_concurrent_purchase_does_not_duplicate_pending_shortage(admin_user, shop):
    order, supplier = shortage(admin_user)
    data = purchase_data(admin_user, order, supplier)

    def create(_):
        close_old_connections()
        try:
            create_purchase(**{**data, "submission_key": uuid.uuid4()})
            return "ok"
        except BusinessError:
            return "blocked"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(create, range(2))) == ["blocked", "ok"]
    assert Purchase.objects.count() == 1


def test_shortage_http_flow_and_stale_lot(admin_user, shop, client):
    order, supplier = shortage(admin_user)
    item = order.items.get()
    client.force_login(admin_user)
    response = client.get(f"/purchases/for-order/{item.pk}/")
    assert response.status_code == 200
    assert response.context["form"].fields["sku"].disabled
    data = dict(
        submission_key=str(uuid.uuid4()),
        supplier=supplier,
        quantity=2,
        unit_cost="80.00",
        condition_description="99 新",
    )
    response = client.post(f"/purchases/for-order/{item.pk}/", data)
    assert response.status_code == 302
    purchase = Purchase.objects.get()
    assert purchase.order_item_id == item.pk
    operate(admin_user, purchase, "order")
    operate(admin_user, purchase, "receive", quantity=2)
    receipt = purchase.receipts.get()
    response = client.get(f"/purchase-receipts/{receipt.pk}/allocate/")
    assert response.status_code == 200
    assert "开单时的货况" in response.content.decode()
    data = reserve_data(admin_user, order, receipt)
    StockLot.objects.filter(pk=receipt.lot_id).update(version=receipt.lot.version + 1)
    with pytest.raises(BusinessError):
        reserve_purchase_receipt(**data)
    response = client.post(
        f"/purchase-receipts/{receipt.pk}/allocate/",
        dict(
            submission_key=str(uuid.uuid4()),
            version=order.version,
            lot_version=receipt.lot.version + 1,
            quantity=2,
            acknowledged="on",
        ),
    )
    assert response.status_code == 302
    assert order.items.get().shortage_qty == 0
