from app.accounts.policies import require_operator
from app.common.business import BusinessError, whole
from app.common.services import execute_once
from app.inventory.models import InventoryBalance, StockLot
from app.inventory.services import move_stock
from app.operations.models import SupplyAllocation
from app.orders.models import OrderItem, Reservation, SalesOrder
from app.orders.services import event, refresh_profit

from .models import PurchaseReceipt


def reserve_purchase_receipt(
    *,
    actor,
    submission_key,
    receipt_id,
    version,
    lot_version,
    quantity,
    acknowledged,
    order_item_id=None,
    request_id="",
):
    require_operator(actor)
    whole(quantity, "备货数量", 1)
    if acknowledged is not True:
        raise BusinessError("请先核对实际货况，确认这组货可用于该订单。")

    def action():
        source = PurchaseReceipt.objects.select_related("purchase").get(pk=receipt_id)
        target_item_id = order_item_id or source.purchase.order_item_id
        if target_item_id is None:
            raise BusinessError("请选择这笔采购已安排的销售订单。")
        original = OrderItem.objects.select_related("order").get(pk=target_item_id)
        # Same order -> SKU balance lock order as sales cancellation and shipment.
        order = SalesOrder.objects.select_for_update().get(pk=original.order_id)
        item = OrderItem.objects.get(pk=original.pk)
        receipt = PurchaseReceipt.objects.select_related("purchase").get(pk=receipt_id)
        if not SupplyAllocation.objects.filter(purchase=receipt.purchase, order_item=item).exists():
            raise BusinessError("这笔采购没有安排给所选销售订单。")
        balance = InventoryBalance.objects.select_for_update().get(sku_id=item.sku_id)
        lot = StockLot.objects.get(pk=receipt.lot_id)
        if order.version != version or lot.version != lot_version:
            raise BusinessError("订单或实物已变化，请刷新后重新核对。")
        if order.status not in (SalesOrder.Status.CONFIRMED, SalesOrder.Status.PARTIAL):
            raise BusinessError("当前订单不能继续备货；采购和库存保留，请单独处理。")
        if lot.sku_id != item.sku_id or quantity > min(item.shortage_qty, lot.available_qty):
            raise BusinessError("备货数量超过订单缺口或这组货的可售库存。")
        Reservation.objects.create(
            item=item,
            lot=lot,
            quantity=quantity,
            unit_cost_fen=lot.unit_cost_fen,
            unit_freight_fen=lot.unit_freight_fen,
            condition_snapshot=lot.condition_snapshot(),
        )
        move_stock(
            balance,
            lot,
            "SALE_RESERVE",
            reserved=quantity,
            reason="核对采购到货后为关联订单备货",
            reference=order.pk,
        )
        item.reserved_qty += quantity
        item.save()
        refresh_profit(order, "order.purchase_reserved")
        event(
            order,
            actor,
            "order.purchase_reserved",
            f"采购 {receipt.purchase.number} 到货已核对，锁定 {quantity} 件；保留开单货况与实际备货货况。",
            request_id,
        )
        return {"order_id": str(order.pk)}

    return execute_once(
        f"purchase.reserve:{actor.pk}",
        submission_key,
        dict(
            receipt_id=str(receipt_id),
            order_item_id=str(order_item_id or "legacy-primary"),
            version=version,
            lot_version=lot_version,
            quantity=quantity,
            acknowledged=acknowledged,
        ),
        action,
    )
