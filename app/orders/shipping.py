from app.common.business import BusinessError, whole
from app.finance.models import MoneyEntry
from app.inventory.models import InventoryBalance, StockLot
from app.inventory.services import move_stock

from .models import Reservation, SalesOrder, Shipment


def dispatch_locked_order(order, *, quantity, delivery_method, carrier, tracking_no, fee_fen):
    """Called inside order_action's transaction while holding the order lock."""
    if order.status not in (SalesOrder.Status.CONFIRMED, SalesOrder.Status.PARTIAL):
        raise BusinessError("请先确认订单并锁定库存。")
    if delivery_method not in ("EXPRESS", "PICKUP", "HANDOVER"):
        raise BusinessError("请选择快递、自提或当面交付。")
    if delivery_method == "EXPRESS" and (not carrier.strip() or not tracking_no.strip()):
        raise BusinessError("快递发货请填写物流公司和运单号。")
    items = list(order.items.order_by("sku_id", "id"))
    if quantity is None:
        if any(item.shortage_qty for item in items):
            raise BusinessError("尚有缺货，请填写本次发货数量，或补货后再整单发货。")
        quantity = sum(item.reserved_qty for item in items)
    whole(quantity, "本次发货数量", 1)
    if quantity > sum(item.reserved_qty for item in items):
        raise BusinessError("本次发货数量超过已备货数量，请先补锁库存。")
    shipment = Shipment(
        order=order,
        quantity=quantity,
        delivery_method=delivery_method,
        carrier=carrier.strip(),
        tracking_no=tracking_no.strip(),
        fee_fen=fee_fen,
        completed=delivery_method != "EXPRESS" and order.channel.code != "XIANYU",
    )
    shipment.full_clean()
    shipment.save()
    remaining = quantity
    for item in items:
        if not remaining:
            break
        balance = InventoryBalance.objects.select_for_update().get(sku_id=item.sku_id)
        for reservation in item.reservations.filter(status="ACTIVE").order_by("created_at", "id"):
            count = min(remaining, reservation.quantity)
            lot = StockLot.objects.get(pk=reservation.lot_id)
            if count < reservation.quantity:
                reservation.quantity -= count
                reservation.save()
                consumed = Reservation.objects.create(
                    item=item,
                    lot=lot,
                    quantity=count,
                    status="CONSUMED",
                    unit_cost_fen=reservation.unit_cost_fen,
                    unit_freight_fen=reservation.unit_freight_fen,
                    condition_snapshot=reservation.condition_snapshot,
                    shipment=shipment,
                )
            else:
                reservation.status = "CONSUMED"
                reservation.shipment = shipment
                reservation.save()
                consumed = reservation
            move_stock(
                balance,
                lot,
                "SALE_SHIPMENT",
                on_hand=-count,
                reserved=-count,
                reason="分批发货/交付出库",
                reference=order.pk,
            )
            shipment.cost_fen += count * (consumed.unit_cost_fen + consumed.unit_freight_fen)
            item.shipped_qty += count
            item.reserved_qty -= count
            remaining -= count
            if not remaining:
                break
        item.save()
    if remaining:
        raise BusinessError("备货记录与数量不一致，请核对库存后再发货。")
    shipment.save()
    order.cost_fen += shipment.cost_fen
    order.fees_fen += fee_fen
    MoneyEntry.objects.create(
        order=order, kind="FEE", amount_fen=fee_fen, reason="本次发货确认实际运费、包装等履约费用"
    )
    order.delivery_method, order.carrier, order.tracking_no = (
        delivery_method,
        carrier.strip(),
        tracking_no.strip(),
    )
    if any(item.shipped_qty + item.cancelled_qty < item.quantity for item in items):
        order.status = SalesOrder.Status.PARTIAL
    else:
        order.status = (
            SalesOrder.Status.SHIPPED
            if Shipment.objects.filter(order=order, completed=False).exists()
            else SalesOrder.Status.COMPLETED
        )
    return f"本次发货 {quantity} 件，已保留本次物流、费用、成本和实际货况。"
