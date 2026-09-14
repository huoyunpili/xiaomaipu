"""Purchase movements: dispatch, arrival and inspection are separate facts."""

from uuid import uuid5

from django.utils import timezone

from app.accounts.policies import require_admin, require_operator
from app.catalog.models import CONDITION_FIELDS
from app.common.business import BusinessError, record_event, whole
from app.common.services import execute_once
from app.inventory.models import StockLot, StockMovement
from app.inventory.services import receive_stock
from app.orders.models import SalesOrder

from .models import (
    DispatchDisposition,
    Purchase,
    PurchaseArrival,
    PurchaseDispatch,
    PurchaseEvent,
    PurchaseInspection,
    PurchaseReceipt,
)


def logistics_action(
    *,
    actor,
    submission_key,
    purchase_id,
    version,
    operation,
    quantity=0,
    dispatch_id=None,
    arrival_id=None,
    occurred_at=None,
    expected_arrival_at=None,
    carrier="",
    tracking_no="",
    result="ACCEPT",
    reason="",
    request_id="",
    **condition,
):
    require_operator(actor)
    if operation not in {
        "ship",
        "arrive",
        "inspect",
        "receive",
        "match",
        "loss",
        "reject_return",
        "promise",
        "eta",
        "source",
        "history_ship",
        "history_finish",
    }:
        raise BusinessError("未知采购物流操作。")
    if operation not in {"promise", "eta"}:
        whole(quantity, "数量", 0 if operation == "history_finish" else 1)
    if operation in {"history_ship", "history_finish"}:
        require_admin(actor)
    if not reason.strip():
        raise BusinessError("请填写实际依据或原因。")
    for value in (occurred_at, expected_arrival_at):
        if value is not None and timezone.is_naive(value):
            raise BusinessError("业务时间必须包含时区。")
    if operation != "promise" and occurred_at and occurred_at > timezone.now():
        raise BusinessError("实际发生时间不能在未来。")
    if (
        operation == "ship"
        and occurred_at
        and expected_arrival_at
        and expected_arrival_at < occurred_at
    ):
        raise BusinessError("预计到货不能早于发货。")
    if len(carrier) > 100 or len(tracking_no) > 100 or len(reason) > 300:
        raise BusinessError("物流信息或说明过长。")

    def action():
        original = Purchase.objects.select_related("order_item").get(pk=purchase_id)
        if original.order_item_id:
            assert original.order_item is not None
            SalesOrder.objects.select_for_update().get(pk=original.order_item.order_id)
        purchase = Purchase.objects.select_for_update().get(pk=purchase_id)
        if purchase.version != version:
            raise BusinessError("采购已变化，请刷新核对。")
        if not purchase.ordered and operation != "promise":
            raise BusinessError("请先确认下单。")
        if purchase.legacy_logistics and operation in {"ship", "arrive", "receive"}:
            raise BusinessError("历史发运余额需先核对，不能猜测重放；已到货仍可验收。")
        dispatch = None
        if dispatch_id:
            dispatch = PurchaseDispatch.objects.filter(pk=dispatch_id, purchase=purchase).first()
            if dispatch is None:
                raise BusinessError("发运批次不属于这张采购单。")
        arrival = None
        if arrival_id:
            arrival = PurchaseArrival.objects.filter(pk=arrival_id, purchase=purchase).first()
            if arrival is None:
                raise BusinessError("到货记录不属于这张采购单。")
        receipt = None
        if operation == "promise":
            purchase.promised_dispatch_at = expected_arrival_at
        elif operation == "eta":
            if dispatch is None:
                raise BusinessError("请选择发运批次。")
            if (
                dispatch.dispatched_at
                and expected_arrival_at
                and expected_arrival_at < dispatch.dispatched_at
            ):
                raise BusinessError("预计到货不能早于发货。")
            dispatch.expected_arrival_at = expected_arrival_at
            dispatch.save()
        elif operation == "source":
            if (
                purchase.legacy_logistics
                or arrival is None
                or arrival.dispatch_id
                or arrival.customer
                or quantity != arrival.quantity
            ):
                raise BusinessError("请选择完整的未匹配到货；历史采购请先补记历史发运。")
            if purchase.shipped_qty + quantity + purchase.cancelled_qty > purchase.quantity:
                raise BusinessError("已有发运与本次来源超过采购数量，请改为关联已有批次。")
            if occurred_at and arrival.received_at and occurred_at > arrival.received_at:
                raise BusinessError("发货不能晚于实际收到时间。")
            dispatch = PurchaseDispatch.objects.create(
                purchase=purchase,
                quantity=quantity,
                received_qty=quantity,
                dispatched_at=occurred_at,
                expected_arrival_at=expected_arrival_at,
                carrier=carrier,
                tracking_no=tracking_no,
                source="RECONCILED",
            )
            arrival.dispatch = dispatch
            arrival.save()
            purchase.shipped_qty += quantity
        elif operation == "history_finish":
            if not purchase.legacy_logistics:
                raise BusinessError("该采购没有历史发运待核对。")
            known = sum(d.quantity for d in purchase.dispatches.all())
            if (
                known + purchase.unmatched_qty + quantity > purchase.quantity
                or purchase.arrived_qty + quantity > purchase.quantity
            ):
                raise BusinessError("已核对发运、未匹配到货及关闭数量超过采购数量。")
            purchase.shipped_qty = known
            purchase.cancelled_qty = quantity
            purchase.closed = quantity > 0
            purchase.legacy_logistics = False
        elif operation == "history_ship":
            if not purchase.legacy_logistics or purchase.direct:
                raise BusinessError("仅管理员可补记待核对的历史本店发运。")
            if sum(d.quantity for d in purchase.dispatches.all()) + quantity > purchase.quantity:
                raise BusinessError("历史发运超出采购数量。")
            dispatch = PurchaseDispatch.objects.create(
                purchase=purchase,
                quantity=quantity,
                dispatched_at=occurred_at,
                expected_arrival_at=expected_arrival_at,
                carrier=carrier,
                tracking_no=tracking_no,
                source="RECONCILED",
            )
        elif operation == "ship":
            if purchase.direct or purchase.closed or purchase.unmatched_qty:
                raise BusinessError("直发请使用直发入口；已关闭或存在未匹配到货时不能新增发运。")
            if quantity > purchase.unshipped_qty:
                raise BusinessError("发货数量超过剩余未发数量。")
            dispatch = PurchaseDispatch.objects.create(
                purchase=purchase,
                quantity=quantity,
                dispatched_at=occurred_at,
                expected_arrival_at=expected_arrival_at,
                carrier=carrier.strip(),
                tracking_no=tracking_no.strip(),
            )
            purchase.shipped_qty += quantity
        elif operation in {"arrive", "receive"}:
            if purchase.direct and (
                operation == "receive" or dispatch is None or not dispatch.customer
            ):
                raise BusinessError("直发只能登记对应客户实际收到，不入本店库存。")
            if purchase.closed and dispatch is None:
                raise BusinessError("已关闭采购只能收取已经发出的批次。")
            if quantity > purchase.quantity - purchase.cancelled_qty - purchase.arrived_qty:
                raise BusinessError("实际收到数量超过有效采购剩余数量。")
            # Explicit combined acceptance remains as a backwards-compatible service, not the default UI.
            if operation == "receive" and dispatch is None:
                candidates = [d for d in purchase.dispatches.all() if d.remaining_qty]
                if len(candidates) == 1 and quantity <= candidates[0].remaining_qty:
                    dispatch = candidates[0]
                elif candidates:
                    raise BusinessError("存在多个或不足量的发运批次，请明确选择本次来源。")
            if dispatch:
                if quantity > dispatch.remaining_qty:
                    raise BusinessError("收到数量超过该批在途数量。")
                if dispatch.dispatched_at and occurred_at and occurred_at < dispatch.dispatched_at:
                    raise BusinessError("实际到货不能早于发货。")
                dispatch.received_qty += quantity
                dispatch.save()
            arrival = PurchaseArrival.objects.create(
                purchase=purchase,
                dispatch=dispatch,
                quantity=quantity,
                received_at=occurred_at,
                customer=purchase.direct,
            )
        elif operation == "match":
            if (
                arrival is None
                or dispatch is None
                or arrival.dispatch_id
                or quantity != arrival.quantity
            ):
                raise BusinessError("请选择完整的未匹配到货记录及其发运批次。")
            if quantity > dispatch.remaining_qty or dispatch.customer != arrival.customer:
                raise BusinessError("匹配数量或收货对象不一致。")
            if (
                dispatch.dispatched_at
                and arrival.received_at
                and arrival.received_at < dispatch.dispatched_at
            ):
                raise BusinessError("实际到货不能早于发货。")
            dispatch.received_qty += quantity
            dispatch.save()
            arrival.dispatch = dispatch
            arrival.save()
        elif operation == "loss":
            if dispatch is None or quantity > dispatch.remaining_qty:
                raise BusinessError("处置数量超过该批在途数量。")
            dispatch.disposed_qty += quantity
            dispatch.save()
            DispatchDisposition.objects.create(
                dispatch=dispatch, quantity=quantity, reason=reason, occurred_at=occurred_at
            )
        elif operation == "reject_return":
            if arrival is None or quantity > arrival.pending_return_qty:
                raise BusinessError("退供数量超过不合格待退数量。")
            arrival.rejected_returned_qty += quantity
            arrival.save()
            purchase.rejected_returned_qty += quantity
        if operation in {"inspect", "receive"}:
            if arrival is None or arrival.customer or quantity > arrival.pending_qty:
                raise BusinessError("验收数量超过本次到货待验数量。")
            if result not in {"ACCEPT", "REJECT"}:
                raise BusinessError("请选择验收结果。")
            if occurred_at and arrival.received_at and occurred_at < arrival.received_at:
                raise BusinessError("验收不能早于到货。")
            if result == "ACCEPT":
                stock = receive_stock(
                    actor=actor,
                    submission_key=uuid5(submission_key, "inspection-stock"),
                    sku_id=purchase.sku_id,
                    quantity=quantity,
                    unit_cost_fen=purchase.unit_cost_fen,
                    label=purchase.number,
                    supplier_name=purchase.supplier_name,
                    reason=reason,
                    **{
                        field: condition.get(field, getattr(purchase, field))
                        for field in CONDITION_FIELDS
                    },
                )
                StockLot.objects.filter(pk=stock["lot_id"]).update(original_received_at=occurred_at)
                receipt = PurchaseReceipt.objects.create(
                    purchase=purchase, lot_id=stock["lot_id"], quantity=quantity
                )
                StockMovement.objects.filter(lot_id=stock["lot_id"], kind="RECEIPT").update(
                    reference_id=str(purchase.pk)
                )
                purchase.received_qty += quantity
            else:
                arrival.rejected_qty += quantity
            arrival.inspected_qty += quantity
            arrival.save()
            PurchaseInspection.objects.create(
                arrival=arrival,
                quantity=quantity,
                result=result,
                inspected_at=occurred_at,
                receipt=receipt,
                reason=reason,
            )
        purchase.version += 1
        purchase.full_clean()
        purchase.save()
        PurchaseEvent.objects.create(
            purchase=purchase, kind=operation, quantity=quantity, reason=reason, receipt=receipt
        )
        record_event(
            actor,
            "purchase." + operation,
            purchase,
            request_id,
            quantity=quantity,
            occurred_at=occurred_at.isoformat() if occurred_at else None,
            dispatch_id=str(dispatch.pk) if dispatch else None,
            arrival_id=str(arrival.pk) if arrival else None,
            expected_arrival_at=expected_arrival_at.isoformat() if expected_arrival_at else None,
        )
        return {
            "purchase_id": str(purchase.pk),
            "dispatch_id": str(dispatch.pk) if dispatch else None,
            "arrival_id": str(arrival.pk) if arrival else None,
        }

    return execute_once(
        f"purchase.logistics:{actor.pk}",
        submission_key,
        dict(
            purchase_id=str(purchase_id),
            version=version,
            operation=operation,
            quantity=quantity,
            dispatch_id=str(dispatch_id or ""),
            arrival_id=str(arrival_id or ""),
            occurred_at=occurred_at.isoformat() if occurred_at else None,
            expected_arrival_at=expected_arrival_at.isoformat() if expected_arrival_at else None,
            carrier=carrier,
            tracking_no=tracking_no,
            result=result,
            reason=reason,
            **condition,
        ),
        action,
    )
