import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, close_old_connections, transaction

from app.catalog.forms import ProductForm
from app.catalog.models import SKU
from app.catalog.services import save_product
from app.common.business import BusinessError
from app.common.forms import to_fen
from app.finance.models import MoneyEntry, ProfitSnapshot
from app.finance.services import record_money
from app.inventory.models import InventoryBalance, StockLot, StockMovement
from app.inventory.services import adjust_stock, edit_lot, receive_stock
from app.orders.models import Reservation, ReturnReceipt, SalesOrder
from app.orders.returns import inspect_return, receive_return
from app.orders.services import create_order, order_action
from app.shops.models import SalesChannel

pytestmark = pytest.mark.django_db


def goods(actor, quantity=2, description="", cost=10000):
    result = save_product(
        actor=actor,
        submission_key=uuid.uuid4(),
        name="测试显示器",
        condition_description=description,
    )
    sku = SKU.objects.get(pk=result["sku_id"])
    receipt = receive_stock(
        actor=actor,
        submission_key=uuid.uuid4(),
        sku_id=sku.pk,
        quantity=quantity,
        unit_cost_fen=cost,
        unit_freight_fen=500,
    )
    return sku, StockLot.objects.get(pk=receipt["lot_id"])


def draft(actor, sku, quantity=1, lot=None, channel="WECHAT", price=20000, **kwargs):
    result = create_order(
        actor=actor,
        submission_key=uuid.uuid4(),
        sku_id=sku.pk,
        quantity=quantity,
        unit_price_fen=price,
        channel_id=SalesChannel.objects.get(code=channel).pk,
        customer_name="测试客人",
        selected_lot_id=lot.pk if lot else None,
        **kwargs,
    )
    return SalesOrder.objects.get(pk=result["order_id"])


def action(actor, order, operation, **kwargs):
    order.refresh_from_db()
    result = order_action(
        actor=actor,
        order_id=order.pk,
        submission_key=uuid.uuid4(),
        action_name=operation,
        version=order.version,
        **kwargs,
    )
    order.refresh_from_db()
    return result


def money(actor, order, kind, amount, reduction=0):
    order.refresh_from_db()
    result = record_money(
        actor=actor,
        submission_key=uuid.uuid4(),
        order_id=order.pk,
        kind=kind,
        amount_fen=amount,
        reduction_fen=reduction,
        version=order.version,
        reason="测试已核对",
    )
    order.refresh_from_db()
    return result


def test_name_only_create_and_free_condition_form(admin_user):
    form = ProductForm({"name": "杂货", "submission_key": str(uuid.uuid4())})
    assert form.is_valid(), form.errors
    result = save_product(actor=admin_user, **form.cleaned_data)
    sku = SKU.objects.get(pk=result["sku_id"])
    assert sku.condition_description == ""
    assert sku.balance.available_qty == 0
    assert sku.code
    changed = save_product(
        actor=admin_user,
        submission_key=uuid.uuid4(),
        name="杂货",
        sku_id=sku.pk,
        version=1,
        condition_description="99 新、无配件、翘边、漏光",
    )
    assert changed == result


def test_inventory_duplicate_receipt_and_lot_selection(admin_user, shop):
    sku, lot = goods(admin_user, description="99 新")
    key = uuid.uuid4()
    kwargs = dict(
        actor=admin_user,
        submission_key=key,
        sku_id=sku.pk,
        quantity=1,
        unit_cost_fen=7000,
        condition_description="无配件，翘边，黑底漏光",
        internal_notes="内部进货备注",
    )
    assert receive_stock(**kwargs) == receive_stock(**kwargs)
    assert sku.lots.count() == 2
    sku.refresh_from_db()
    assert sku.requires_explicit_lot_selection
    with pytest.raises(BusinessError):
        draft(admin_user, sku)
    chosen = sku.lots.exclude(pk=lot.pk).get()
    order = draft(admin_user, sku, lot=chosen)
    assert "internal_notes" not in order.items.get().condition_snapshot
    action(admin_user, order, "confirm")
    assert Reservation.objects.get(item__order=order).lot_id == chosen.pk
    lot.refresh_from_db()
    assert lot.reserved_qty == 0


def test_last_item_no_double_deduct_and_partial_payment(admin_user, shop):
    sku, lot = goods(admin_user, quantity=1)
    order = draft(admin_user, sku)
    balance = InventoryBalance.objects.get(sku=sku)
    assert balance.available_qty == 1  # draft does not reserve
    action(admin_user, order, "confirm")
    balance.refresh_from_db()
    assert (balance.on_hand_qty, balance.reserved_qty, balance.available_qty) == (1, 1, 0)
    money(admin_user, order, "RECEIPT", 5000)
    assert order.outstanding_fen == 15000
    assert order.realized_profit_fen is None
    action(admin_user, order, "ship", delivery_method="HANDOVER", fulfillment_fee_fen=1000)
    balance.refresh_from_db()
    assert (balance.on_hand_qty, balance.reserved_qty, balance.available_qty) == (0, 0, 0)
    assert order.status == "COMPLETED" and order.realized_profit_fen is None
    money(admin_user, order, "RECEIPT", 15000)
    assert order.realized_profit_fen == 8500
    assert order.cost_fen == 10500
    assert ProfitSnapshot.objects.filter(order=order).count() == 4
    with pytest.raises(BusinessError):
        action(admin_user, order, "cancel", reason="已交付不能取消")


def test_shortage_cancel_release_and_reallocate(admin_user, shop):
    sku, lot = goods(admin_user, quantity=1)
    order = draft(admin_user, sku, quantity=2)
    action(admin_user, order, "confirm")
    assert order.items.get().shortage_qty == 1
    with pytest.raises(BusinessError):
        action(admin_user, order, "ship", delivery_method="HANDOVER")
    receive_stock(
        actor=admin_user,
        submission_key=uuid.uuid4(),
        sku_id=sku.pk,
        quantity=1,
        unit_cost_fen=15000,
    )
    action(admin_user, order, "confirm")
    assert order.items.get().shortage_qty == 0
    action(admin_user, order, "cancel", reason="客户不要了")
    assert InventoryBalance.objects.get(sku=sku).available_qty == 2
    assert not Reservation.objects.filter(status="ACTIVE").exists()


def test_selected_lot_shortage_does_not_use_other_conditions(admin_user, shop):
    sku, lot = goods(admin_user, quantity=1, description="99 新")
    receive_stock(
        actor=admin_user,
        submission_key=uuid.uuid4(),
        sku_id=sku.pk,
        quantity=9,
        unit_cost_fen=5000,
        condition_description="漏光严重",
    )
    order = draft(admin_user, sku, quantity=2, lot=lot)
    action(admin_user, order, "confirm")
    assert order.items.get().shortage_qty == 1
    assert Reservation.objects.filter(item__order=order).get().lot_id == lot.pk


def test_condition_edit_preserves_history_and_blocks_locked_stock(admin_user, shop):
    sku, lot = goods(admin_user, quantity=2, description="99 新")
    order = draft(admin_user, sku, lot=lot)
    action(admin_user, order, "confirm")
    lot.refresh_from_db()
    with pytest.raises(BusinessError):
        edit_lot(
            actor=admin_user,
            submission_key=uuid.uuid4(),
            lot_id=lot.pk,
            version=lot.version,
            condition_description="发现漏光",
        )
    action(admin_user, order, "ship", delivery_method="HANDOVER")
    lot.refresh_from_db()
    edit_lot(
        actor=admin_user,
        submission_key=uuid.uuid4(),
        lot_id=lot.pk,
        version=lot.version,
        condition_description="剩余这件有漏光",
    )
    assert order.items.get().condition_snapshot["condition_description"] == "99 新"
    assert (
        Reservation.objects.get(item__order=order).condition_snapshot["condition_description"]
        == "99 新"
    )
    assert SalesOrder.objects.get(pk=order.pk).cost_fen == 10500


def test_changed_draft_condition_requires_review(admin_user, shop):
    sku, lot = goods(admin_user, quantity=1, description="99 新")
    order = draft(admin_user, sku, lot=lot)
    edit_lot(
        actor=admin_user,
        submission_key=uuid.uuid4(),
        lot_id=lot.pk,
        version=lot.version,
        condition_description="发现翘边",
    )
    with pytest.raises(BusinessError):
        action(admin_user, order, "confirm")
    assert InventoryBalance.objects.get(sku=sku).reserved_qty == 0


def test_refund_return_inspection_and_cost_recovery(admin_user, shop):
    sku, lot = goods(admin_user, quantity=1)
    order = draft(admin_user, sku)
    action(admin_user, order, "confirm")
    action(admin_user, order, "ship", delivery_method="HANDOVER", fulfillment_fee_fen=1000)
    money(admin_user, order, "RECEIPT", 20000)
    money(admin_user, order, "REFUND", 20000, reduction=20000)
    assert order.realized_profit_fen == -11500
    reservation = Reservation.objects.get(item__order=order)
    result = receive_return(
        actor=admin_user,
        submission_key=uuid.uuid4(),
        reservation_id=reservation.pk,
        quantity=1,
        reason="收到退货",
    )
    balance = InventoryBalance.objects.get(sku=sku)
    assert (balance.available_qty, balance.inspection_qty) == (0, 1)
    key = uuid.uuid4()
    kwargs = dict(
        actor=admin_user,
        submission_key=key,
        return_id=result["return_id"],
        result="RESTOCKED",
        reason="确认可再次销售",
        condition_description="退回后轻微翘边",
    )
    assert inspect_return(**kwargs) == inspect_return(**kwargs)
    order.refresh_from_db()
    balance.refresh_from_db()
    assert (balance.available_qty, balance.inspection_qty) == (1, 0)
    assert order.realized_profit_fen == -1000
    assert ReturnReceipt.objects.get().restocked_lot.condition_description == "退回后轻微翘边"
    with pytest.raises(BusinessError):
        receive_return(
            actor=admin_user,
            submission_key=uuid.uuid4(),
            reservation_id=reservation.pk,
            quantity=1,
            reason="不能再退一次",
        )


def test_scrapped_return_does_not_restore_sellable_stock(admin_user, shop):
    sku, lot = goods(admin_user, quantity=1)
    order = draft(admin_user, sku)
    action(admin_user, order, "confirm")
    action(admin_user, order, "ship", delivery_method="HANDOVER")
    receipt = receive_return(
        actor=admin_user,
        submission_key=uuid.uuid4(),
        reservation_id=Reservation.objects.get(item__order=order).pk,
        quantity=1,
        reason="退回破损",
    )
    inspect_return(
        actor=admin_user,
        submission_key=uuid.uuid4(),
        return_id=receipt["return_id"],
        result="SCRAPPED",
        reason="无法销售",
    )
    balance = InventoryBalance.objects.get(sku=sku)
    assert balance.available_qty == balance.inspection_qty == 0


def test_overpayment_not_profit_and_refund_does_not_double_reduce_due(admin_user, shop):
    sku, lot = goods(admin_user, quantity=1)
    order = draft(admin_user, sku)
    action(admin_user, order, "confirm")
    action(admin_user, order, "ship", delivery_method="HANDOVER")
    money(admin_user, order, "RECEIPT", 22000)
    assert order.payment_label == "超额收款待处理"
    assert order.realized_profit_fen is None
    money(admin_user, order, "REFUND", 2000)
    assert order.adjusted_due_fen == 20000
    assert order.realized_profit_fen == 9500
    with pytest.raises(BusinessError):
        money(admin_user, order, "REFUND", 30000)


def test_platform_payment_is_escrow_and_needs_transaction_success(admin_user, shop):
    sku, lot = goods(admin_user, quantity=1)
    order = draft(admin_user, sku, channel="XIANYU")
    with pytest.raises(BusinessError):
        action(admin_user, order, "confirm")
    action(admin_user, order, "confirm", reason="已核对买家付款")
    assert order.payment_label == "平台托管待结算" and order.received_fen == 0
    action(admin_user, order, "ship", delivery_method="PICKUP")
    assert order.status == "SHIPPED"
    with pytest.raises(BusinessError):
        action(admin_user, order, "complete")
    action(admin_user, order, "complete", reason="已核对平台交易成功")
    assert order.realized_profit_fen is None
    money(admin_user, order, "RECEIPT", 20000)
    assert order.realized_profit_fen == 9500


def test_adjustment_permission_locked_stock_and_stale_count(admin_user, operator, shop):
    sku, lot = goods(admin_user, quantity=2)
    with pytest.raises(PermissionDenied):
        adjust_stock(
            actor=operator,
            submission_key=uuid.uuid4(),
            lot_id=lot.pk,
            actual_qty=1,
            version=lot.version,
            reason="盘点",
        )
    order = draft(admin_user, sku)
    action(admin_user, order, "confirm")
    lot.refresh_from_db()
    with pytest.raises(BusinessError):
        adjust_stock(
            actor=admin_user,
            submission_key=uuid.uuid4(),
            lot_id=lot.pk,
            actual_qty=0,
            version=lot.version,
            reason="盘亏",
        )
    adjust_stock(
        actor=admin_user,
        submission_key=uuid.uuid4(),
        lot_id=lot.pk,
        actual_qty=1,
        version=lot.version,
        reason="盘亏未锁定件",
    )
    assert InventoryBalance.objects.get(sku=sku).available_qty == 0
    assert StockMovement.objects.get(kind="COUNT_LOSS").on_hand_delta == -1


def test_dispatch_failure_rolls_back_stock_cost_and_records(admin_user, shop):
    sku, lot = goods(admin_user, quantity=1)
    order = draft(admin_user, sku)
    action(admin_user, order, "confirm")
    with (
        patch("app.orders.services.event", side_effect=RuntimeError("audit unavailable")),
        pytest.raises(RuntimeError),
    ):
        action(admin_user, order, "ship", delivery_method="HANDOVER")
    order.refresh_from_db()
    balance = InventoryBalance.objects.get(sku=sku)
    assert order.status == "CONFIRMED" and order.cost_fen == 0
    assert (balance.on_hand_qty, balance.reserved_qty) == (1, 1)
    assert not StockMovement.objects.filter(kind="SALE_SHIPMENT").exists()
    assert not MoneyEntry.objects.exists()


def test_repeat_money_and_dispatch_are_idempotent(admin_user, shop):
    sku, lot = goods(admin_user, quantity=1)
    order = draft(admin_user, sku)
    action(admin_user, order, "confirm")
    data = dict(
        actor=admin_user,
        submission_key=uuid.uuid4(),
        order_id=order.pk,
        action_name="ship",
        version=order.version,
        delivery_method="HANDOVER",
    )
    assert order_action(**data) == order_action(**data)
    order.refresh_from_db()
    receipt = dict(
        actor=admin_user,
        submission_key=uuid.uuid4(),
        order_id=order.pk,
        kind="RECEIPT",
        version=order.version,
        amount_fen=20000,
        reason="实际到账",
    )
    assert record_money(**receipt) == record_money(**receipt)
    assert MoneyEntry.objects.filter(kind="RECEIPT").count() == 1
    assert StockMovement.objects.filter(kind="SALE_SHIPMENT").count() == 1


@pytest.mark.django_db(transaction=True)
def test_two_channels_compete_for_last_item(admin_user, shop):
    sku, lot = goods(admin_user, quantity=1)
    orders = [draft(admin_user, sku, channel=channel) for channel in ("WECHAT", "OFFLINE")]

    def confirm(order):
        close_old_connections()
        try:
            action(admin_user, order, "confirm")
            return order.items.get().reserved_qty
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(confirm, orders))
    assert sorted(results) == [0, 1]
    balance = InventoryBalance.objects.get(sku=sku)
    assert (balance.on_hand_qty, balance.reserved_qty, balance.available_qty) == (1, 1, 0)


def test_db_constraints_and_exact_currency(admin_user):
    sku, lot = goods(admin_user, quantity=1)
    with pytest.raises(IntegrityError), transaction.atomic():
        InventoryBalance.objects.filter(sku=sku).update(reserved_qty=2)
    assert to_fen(Decimal("0.29")) == 29
    with pytest.raises(BusinessError):
        receive_stock(
            actor=admin_user,
            submission_key=uuid.uuid4(),
            sku_id=sku.pk,
            quantity=-1,
            unit_cost_fen=100,
        )


def test_external_order_number_deduplication(admin_user, shop):
    sku, lot = goods(admin_user)
    draft(admin_user, sku, external_order_no="same-external")
    with pytest.raises(BusinessError):
        draft(admin_user, sku, external_order_no="same-external")
    assert SalesOrder.objects.count() == 1


def test_completed_cost_change_requires_admin(admin_user, operator, shop):
    sku, lot = goods(admin_user)
    order = draft(admin_user, sku)
    action(admin_user, order, "confirm")
    action(admin_user, order, "ship", delivery_method="HANDOVER")
    with pytest.raises(PermissionDenied):
        money(operator, order, "FEE", 100)
    money(operator, order, "RECEIPT", 20000)
    assert order.realized_profit_fen == 9500


def test_business_pages_permissions_and_refund_forms(client, admin_user, operator, shop):
    sku, lot = goods(admin_user, quantity=1)
    order = draft(admin_user, sku)
    assert client.get(f"/products/{sku.pk}/").status_code == 302
    client.force_login(admin_user)
    assert client.get(f"/orders/{order.pk}/").status_code == 200
    action(admin_user, order, "confirm")
    action(admin_user, order, "ship", delivery_method="HANDOVER")
    money(admin_user, order, "RECEIPT", 20000)
    response = client.post(
        f"/orders/{order.pk}/money/REFUND/",
        {
            "submission_key": str(uuid.uuid4()),
            "version": order.version,
            "amount": "20.00",
            "reduction": "20.00",
            "reason": "漏光补偿",
        },
    )
    assert response.status_code == 302
    order.refresh_from_db()
    assert order.realized_profit_fen == 7500
    reservation = Reservation.objects.get(item__order=order)
    response = client.post(
        f"/returns/receive/{reservation.pk}/",
        {
            "submission_key": str(uuid.uuid4()),
            "quantity": 1,
            "reason": "实际收到",
        },
    )
    assert response.status_code == 302
    receipt = ReturnReceipt.objects.get()
    assert client.get(f"/returns/inspect/{receipt.pk}/").status_code == 200
    client.force_login(operator)
    assert client.get(f"/inventory/{lot.pk}/adjust/").status_code == 403
    assert client.get(f"/returns/inspect/{receipt.pk}/").status_code == 403
    assert client.post(f"/orders/{order.pk}/money/REFUND/", {}).status_code == 403
    assert client.get(f"/orders/{order.pk}/actions/invalid/").status_code == 404


def test_cancelled_prepaid_order_keeps_refund_due(admin_user, shop):
    sku, lot = goods(admin_user, quantity=1)
    order = draft(admin_user, sku)
    action(admin_user, order, "confirm")
    money(admin_user, order, "RECEIPT", 5000)
    action(admin_user, order, "cancel", reason="客户取消")
    assert order.net_received_fen == 5000 and order.adjusted_due_fen == 0
    assert order.payment_label == "超额收款待处理"
    money(admin_user, order, "REFUND", 5000)
    assert order.net_received_fen == 0 and order.realized_profit_fen is None
    assert InventoryBalance.objects.get(sku=sku).available_qty == 1
