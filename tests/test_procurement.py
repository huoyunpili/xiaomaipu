import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from django.db import close_old_connections

from app.catalog.models import SKU
from app.catalog.services import save_product
from app.common.business import BusinessError
from app.common.services import IdempotencyConflict
from app.inventory.models import InventoryBalance, StockLot, StockMovement
from app.procurement.models import Purchase, PurchaseEvent, PurchaseReceipt, SupplierQuote
from app.procurement.services import add_quote, create_purchase, purchase_action, save_supplier
from tests.test_business import action as sale_action
from tests.test_business import draft, money

pytestmark = pytest.mark.django_db


def setup_purchase(actor, quantity=3):
    supplier = save_supplier(actor=actor, submission_key=uuid.uuid4(), name="测试供货人")
    product = save_product(actor=actor, submission_key=uuid.uuid4(), name="测试二手显示器")
    result = create_purchase(
        actor=actor,
        submission_key=uuid.uuid4(),
        supplier_id=supplier["supplier_id"],
        sku_id=product["sku_id"],
        quantity=quantity,
        unit_cost_fen=10500,
        condition_description="有轻微漏光",
        condition_label="99 新",
    )
    return Purchase.objects.get(pk=result["purchase_id"])


def operate(actor, purchase, operation, **kwargs):
    purchase.refresh_from_db()
    result = purchase_action(
        actor=actor,
        submission_key=uuid.uuid4(),
        purchase_id=purchase.pk,
        version=purchase.version,
        operation=operation,
        reason="实际核对",
        **kwargs,
    )
    purchase.refresh_from_db()
    return result


def test_partial_receipt_payment_close_refund_and_trace(admin_user):
    purchase = setup_purchase(admin_user)
    assert InventoryBalance.objects.get().available_qty == 0
    with pytest.raises(BusinessError):
        operate(admin_user, purchase, "receive", quantity=1)
    operate(admin_user, purchase, "order")
    operate(admin_user, purchase, "pay", amount_fen=10000)
    operate(admin_user, purchase, "pay", amount_fen=21500)
    with pytest.raises(BusinessError):
        operate(admin_user, purchase, "pay", amount_fen=1)
    operate(admin_user, purchase, "ship", quantity=3)
    assert purchase.in_transit_qty == 3
    assert InventoryBalance.objects.get().available_qty == 0
    operate(
        admin_user,
        purchase,
        "receive",
        quantity=1,
        condition_description="无配件，翘边",
        condition_label="95 新",
    )
    assert (purchase.received_qty, purchase.in_transit_qty, purchase.pending_qty) == (1, 2, 2)
    lot = StockLot.objects.get()
    assert (lot.condition_label, lot.unit_cost_fen, lot.unit_freight_fen) == ("95 新", 10500, 0)
    assert StockMovement.objects.get().reference_id == str(purchase.pk)
    with pytest.raises(BusinessError):
        operate(admin_user, purchase, "receive", quantity=3)
    with pytest.raises(BusinessError):
        operate(admin_user, purchase, "close")
    assert purchase.in_transit_qty == 2
    assert InventoryBalance.objects.get().available_qty == 1
    # All remaining goods have already been dispatched: cancellation cannot erase them.
    operate(admin_user, purchase, "receive", quantity=2)
    assert purchase.pending_qty == purchase.in_transit_qty == 0


def test_receipt_idempotency_and_atomic_failure(admin_user):
    purchase = setup_purchase(admin_user)
    operate(admin_user, purchase, "order")
    data = dict(
        actor=admin_user,
        submission_key=uuid.uuid4(),
        purchase_id=purchase.pk,
        version=purchase.version,
        operation="receive",
        quantity=1,
        reason="已收到",
    )
    with patch("app.procurement.services.PurchaseEvent.save", side_effect=RuntimeError("rollback")):
        with pytest.raises(RuntimeError):
            purchase_action(**data)
    assert not StockLot.objects.exists()
    assert not PurchaseReceipt.objects.exists()
    assert InventoryBalance.objects.get().available_qty == 0
    assert purchase_action(**data) == purchase_action(**data)
    assert PurchaseReceipt.objects.count() == 1
    assert StockMovement.objects.count() == 1
    with pytest.raises(IdempotencyConflict):
        purchase_action(**{**data, "quantity": 2})


def test_quote_history_does_not_rewrite_purchase_or_sale_cost(admin_user, shop):
    purchase = setup_purchase(admin_user, quantity=1)
    quote = add_quote(
        actor=admin_user,
        submission_key=uuid.uuid4(),
        supplier_id=purchase.supplier_id,
        sku_id=purchase.sku_id,
        unit_cost_fen=10500,
        condition_description="报价货况",
    )
    data = dict(
        actor=admin_user,
        submission_key=uuid.uuid4(),
        supplier_id=purchase.supplier_id,
        sku_id=purchase.sku_id,
        quote_id=quote["quote_id"],
        quantity=1,
        unit_cost_fen=10500,
    )
    assert create_purchase(**data) == create_purchase(**data)
    operate(admin_user, purchase, "order")
    operate(admin_user, purchase, "receive", quantity=1)
    order = draft(admin_user, SKU.objects.get(pk=purchase.sku_id), lot=StockLot.objects.get())
    sale_action(admin_user, order, "confirm")
    sale_action(admin_user, order, "ship", delivery_method="HANDOVER")
    money(admin_user, order, "RECEIPT", 20000)
    add_quote(
        actor=admin_user,
        submission_key=uuid.uuid4(),
        supplier_id=purchase.supplier_id,
        sku_id=purchase.sku_id,
        unit_cost_fen=6000,
    )
    order.refresh_from_db()
    assert order.cost_fen == 10500 and order.realized_profit_fen == 9500
    assert SupplierQuote.objects.count() == 2
    assert set(Purchase.objects.values_list("unit_cost_fen", flat=True)) == {10500}


def test_purchase_return_blocks_reserved_stock_and_refunds_separately(admin_user, shop):
    purchase = setup_purchase(admin_user, quantity=2)
    operate(admin_user, purchase, "order")
    operate(admin_user, purchase, "pay", amount_fen=21000)
    operate(admin_user, purchase, "receive", quantity=2)
    receipt = PurchaseReceipt.objects.get()
    order = draft(admin_user, purchase.sku, quantity=1, lot=receipt.lot)
    sale_action(admin_user, order, "confirm")
    with pytest.raises(BusinessError):
        operate(admin_user, purchase, "return", quantity=2, receipt_id=receipt.pk)
    operate(admin_user, purchase, "return", quantity=1, receipt_id=receipt.pk)
    assert purchase.refund_due_fen == 10500 and purchase.net_paid_fen == 21000
    assert InventoryBalance.objects.get().available_qty == 0
    assert StockMovement.objects.get(kind="PURCHASE_RETURN").on_hand_delta == -1
    sale_action(admin_user, order, "cancel", reason="取消销售")
    operate(admin_user, purchase, "return", quantity=1, receipt_id=receipt.pk)
    operate(admin_user, purchase, "refund", amount_fen=21000)
    assert purchase.total_fen == purchase.net_paid_fen == 0
    assert StockLot.objects.get().on_hand_qty == 0
    with pytest.raises(BusinessError):
        operate(admin_user, purchase, "return", quantity=1, receipt_id=receipt.pk)


@pytest.mark.django_db(transaction=True)
def test_concurrent_receipt_only_one_submission_wins(admin_user):
    purchase = setup_purchase(admin_user, quantity=1)
    operate(admin_user, purchase, "order")

    def receive(_):
        close_old_connections()
        try:
            purchase_action(
                actor=admin_user,
                submission_key=uuid.uuid4(),
                purchase_id=purchase.pk,
                version=purchase.version,
                operation="receive",
                quantity=1,
                reason="并发收货",
            )
            return "ok"
        except BusinessError:
            return "stale"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(receive, range(2))) == ["ok", "stale"]
    assert InventoryBalance.objects.get().available_qty == 1
    assert PurchaseReceipt.objects.count() == 1


def test_procurement_pages_and_stale_submission(client, admin_user):
    purchase = setup_purchase(admin_user)
    assert client.get("/purchases/").status_code == 302
    client.force_login(admin_user)
    for url in (
        "/purchases/",
        "/purchases/new/",
        "/suppliers/",
        "/suppliers/new/",
        f"/suppliers/{purchase.supplier_id}/",
        f"/suppliers/{purchase.supplier_id}/quotes/new/",
        f"/purchases/{purchase.pk}/",
        f"/purchases/{purchase.pk}/receive/",
    ):
        assert client.get(url).status_code == 200
    data = dict(submission_key=str(uuid.uuid4()), version=purchase.version, reason="已下单")
    assert client.post(f"/purchases/{purchase.pk}/order/", data).status_code == 302
    assert client.post(f"/purchases/{purchase.pk}/order/", data).status_code == 302
    data["submission_key"] = str(uuid.uuid4())
    assert client.post(f"/purchases/{purchase.pk}/order/", data).status_code == 409
    assert client.get(f"/purchases/{purchase.pk}/invalid/").status_code == 404
    assert PurchaseEvent.objects.filter(kind="order").count() == 1
