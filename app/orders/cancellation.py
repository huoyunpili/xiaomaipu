from app.common.business import BusinessError, whole
from app.finance.models import MoneyEntry
from app.inventory.models import InventoryBalance, StockLot
from app.inventory.services import move_stock

from .models import SalesOrder, Shipment


def cancel_remaining_locked(order, *, reduction_fen, reason):
    whole(reduction_fen, "本次减免应收")
    if order.status != SalesOrder.Status.PARTIAL or not reason.strip():
        raise BusinessError("仅部分发货订单可关闭剩余数量，请填写原因。")
    if reduction_fen > order.adjusted_due_fen:
        raise BusinessError("本次减免不能超过当前应收；已有折扣或退款减免请核对后填写。")
    count = 0
    for item in order.items.order_by("sku_id", "id"):
        balance = InventoryBalance.objects.select_for_update().get(sku_id=item.sku_id)
        remaining = item.quantity - item.shipped_qty - item.cancelled_qty
        for reservation in item.reservations.filter(status="ACTIVE"):
            lot = StockLot.objects.get(pk=reservation.lot_id)
            move_stock(
                balance,
                lot,
                "SALE_RELEASE",
                reserved=-reservation.quantity,
                reason=reason,
                reference=order.pk,
            )
            reservation.status = "RELEASED"
            reservation.save()
        item.cancelled_qty += remaining
        item.reserved_qty = 0
        item.save()
        count += remaining
    if not count:
        raise BusinessError("没有可关闭的未发数量。")
    order.amount_reduction_fen += reduction_fen
    if reduction_fen:
        MoneyEntry.objects.create(
            order=order,
            kind="REFUND",
            amount_fen=0,
            reduction_fen=reduction_fen,
            reason="关闭剩余未发数量：" + reason,
        )
    order.status = (
        SalesOrder.Status.SHIPPED
        if Shipment.objects.filter(order=order, completed=False).exists()
        else SalesOrder.Status.COMPLETED
    )
    return f"关闭未发 {count} 件并释放备货，减免应收 {reduction_fen / 100:.2f} 元；实际退款另行登记，关联采购保留。"
