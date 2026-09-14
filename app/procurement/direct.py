from app.accounts.policies import require_operator
from app.catalog.models import CONDITION_FIELDS
from app.common.business import BusinessError, record_event, whole
from app.common.services import execute_once
from app.finance.models import MoneyEntry
from app.orders.models import OrderItem, Reservation, SalesOrder, Shipment
from app.orders.services import event, refresh_profit

from .models import Purchase, PurchaseDispatch, PurchaseEvent


def dispatch_direct(
    *,
    actor,
    submission_key,
    purchase_id,
    version,
    order_version,
    quantity,
    carrier,
    tracking_no,
    occurred_at=None,
    expected_arrival_at=None,
    fee_fen=0,
    evidence_note="",
    acknowledged=False,
    request_id="",
    **condition,
):
    require_operator(actor)
    from django.utils import timezone

    for value in (occurred_at, expected_arrival_at):
        if value is not None and timezone.is_naive(value):
            raise BusinessError("业务时间必须包含时区。")
    if occurred_at and occurred_at > timezone.now():
        raise BusinessError("实际发货时间不能在未来。")
    if occurred_at and expected_arrival_at and expected_arrival_at < occurred_at:
        raise BusinessError("预计到货不能早于发货。")
    whole(quantity, "直发数量", 1)
    whole(fee_fen, "直发费用")
    if (
        not carrier.strip()
        or not tracking_no.strip()
        or not evidence_note.strip()
        or acknowledged is not True
    ):
        raise BusinessError(
            "请核对直发货况，填写物流、运单号和取证说明（可写供应商直发未留证原因）。"
        )

    def action():
        original = Purchase.objects.select_related("order_item").get(pk=purchase_id)
        if not original.direct or not original.order_item:
            raise BusinessError("这不是关联订单的直发采购。")
        order = SalesOrder.objects.select_for_update().get(pk=original.order_item.order_id)
        purchase = Purchase.objects.select_for_update().get(pk=purchase_id)
        assert purchase.order_item_id is not None
        item = OrderItem.objects.get(pk=purchase.order_item_id)
        if purchase.version != version or order.version != order_version:
            raise BusinessError("采购或订单已变化，请刷新后核对。")
        if order.status not in ("CONFIRMED", "PARTIAL") or not purchase.ordered or purchase.closed:
            raise BusinessError("请先确认采购下单及销售订单，已关闭订单不可直发。")
        if quantity > min(purchase.pending_qty, item.shortage_qty):
            raise BusinessError("直发数量超过采购剩余数量或订单缺口。")
        shipment = Shipment(
            order=order,
            purchase=purchase,
            quantity=quantity,
            delivery_method="EXPRESS",
            carrier=carrier.strip(),
            tracking_no=tracking_no.strip(),
            cost_fen=quantity * purchase.unit_cost_fen,
            fee_fen=fee_fen,
            evidence_note=evidence_note,
        )
        shipment.full_clean()
        shipment.save()
        Reservation.objects.create(
            item=item,
            quantity=quantity,
            status="CONSUMED",
            shipment=shipment,
            unit_cost_fen=purchase.unit_cost_fen,
            unit_freight_fen=0,
            condition_snapshot={
                field: condition.get(field, getattr(purchase, field))
                for field in CONDITION_FIELDS
                if field != "internal_notes"
            },
        )
        PurchaseDispatch.objects.create(
            purchase=purchase,
            quantity=quantity,
            customer=True,
            shipment=shipment,
            dispatched_at=occurred_at,
            expected_arrival_at=expected_arrival_at,
            carrier=carrier.strip(),
            tracking_no=tracking_no.strip(),
        )
        purchase.direct_qty += quantity
        purchase.shipped_qty += quantity
        purchase.version += 1
        purchase.full_clean()
        purchase.save()
        item.shipped_qty += quantity
        item.save()
        order.cost_fen += shipment.cost_fen
        order.fees_fen += fee_fen
        order.status = (
            "PARTIAL"
            if any(i.shipped_qty + i.cancelled_qty < i.quantity for i in order.items.all())
            else "SHIPPED"
        )
        MoneyEntry.objects.create(
            order=order, kind="FEE", amount_fen=fee_fen, reason="供应商直发实际履约费用"
        )
        PurchaseEvent.objects.create(
            purchase=purchase, kind="direct", quantity=quantity, reason="供应商已直接发给买家"
        )
        record_event(actor, "purchase.direct", purchase, request_id, quantity=quantity)
        refresh_profit(order, "order.direct")
        event(
            order,
            actor,
            "order.direct",
            f"供应商直发 {quantity} 件，不变动自有库存，采购 {purchase.number}。",
            request_id,
        )
        return {"order_id": str(order.pk)}

    return execute_once(
        f"purchase.direct:{actor.pk}",
        submission_key,
        dict(
            purchase_id=str(purchase_id),
            version=version,
            order_version=order_version,
            quantity=quantity,
            carrier=carrier,
            tracking_no=tracking_no,
            occurred_at=occurred_at.isoformat() if occurred_at else None,
            expected_arrival_at=expected_arrival_at.isoformat() if expected_arrival_at else None,
            fee_fen=fee_fen,
            evidence_note=evidence_note,
            acknowledged=acknowledged,
            **condition,
        ),
        action,
    )
