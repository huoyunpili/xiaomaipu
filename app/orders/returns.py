from django.utils import timezone

from app.accounts.policies import require_admin, require_operator
from app.catalog.models import CONDITION_FIELDS, SKU
from app.common.business import BusinessError, whole
from app.common.services import execute_once
from app.inventory.models import InventoryBalance, StockLot
from app.inventory.services import move_stock

from .models import Reservation, ReturnReceipt, SalesOrder
from .services import event, refresh_profit


def receive_return(*, actor, submission_key, reservation_id, quantity, reason, request_id=""):
    require_operator(actor)
    whole(quantity, "退货数量", 1)
    if not reason.strip():
        raise BusinessError("请填写退货原因。")

    def action():
        original = Reservation.objects.select_related("item").get(pk=reservation_id)
        order = SalesOrder.objects.select_for_update().get(pk=original.item.order_id)
        reservation = Reservation.objects.get(pk=reservation_id)
        if (
            reservation.status != "CONSUMED"
            or quantity > reservation.quantity - reservation.returned_qty
        ):
            raise BusinessError("退货数量不能超过这批实际出库且尚未退回的数量。")
        balance = InventoryBalance.objects.select_for_update().get(sku_id=original.item.sku_id)
        if reservation.lot_id:
            lot = StockLot.objects.get(pk=reservation.lot_id)
        else:
            lot = StockLot.objects.create(
                sku_id=balance.sku_id,
                label="直发退货待检",
                unit_cost_fen=reservation.unit_cost_fen,
                unit_freight_fen=reservation.unit_freight_fen,
                received_qty=quantity,
                on_hand_qty=0,
                **reservation.condition_snapshot,
            )
        receipt = ReturnReceipt.objects.create(
            reservation=reservation, quantity=quantity, reason=reason, inspection_lot=lot
        )
        reservation.returned_qty += quantity
        reservation.save()
        move_stock(
            balance,
            lot,
            "RETURN_INSPECTION",
            inspection=quantity,
            reason=reason,
            reference=order.pk,
        )
        event(order, actor, "return.received", "退货已收到，进入待检库存，尚不可售。", request_id)
        return {"order_id": str(order.pk), "return_id": str(receipt.pk)}

    return execute_once(
        f"return.receive:{actor.pk}",
        submission_key,
        {"reservation_id": str(reservation_id), "quantity": quantity, "reason": reason},
        action,
    )


def inspect_return(*, actor, submission_key, return_id, result, reason, request_id="", **condition):
    require_admin(actor)
    if result not in ("RESTOCKED", "SCRAPPED") or not reason.strip():
        raise BusinessError("请选择可售入库或不可售报废，并说明原因；仍待维修的货先保留待检。")

    def action():
        original = ReturnReceipt.objects.select_related("reservation__item").get(pk=return_id)
        order = SalesOrder.objects.select_for_update().get(pk=original.reservation.item.order_id)
        receipt = ReturnReceipt.objects.select_related("reservation").get(pk=return_id)
        if receipt.status != "INSPECTION":
            raise BusinessError("这笔退货已经验收，不能重复入库。")
        reservation = receipt.reservation
        balance = InventoryBalance.objects.select_for_update().get(
            sku_id=original.reservation.item.sku_id
        )
        source_lot_id = receipt.inspection_lot_id or reservation.lot_id
        assert source_lot_id is not None
        old_lot = StockLot.objects.get(pk=source_lot_id)
        if result == "RESTOCKED":
            descriptions = {
                field: condition.get(field, reservation.condition_snapshot.get(field, ""))
                for field in CONDITION_FIELDS
            }
            lot = StockLot(
                sku_id=balance.sku_id,
                label="退货验收可售",
                original_received_at=old_lot.original_received_at,
                last_restocked_at=timezone.now(),
                origin_lot=old_lot.origin_lot or old_lot,
                supplier_name=old_lot.supplier_name,
                unit_cost_fen=reservation.unit_cost_fen,
                unit_freight_fen=reservation.unit_freight_fen,
                received_qty=receipt.quantity,
                on_hand_qty=0,
                **descriptions,
            )
            lot.full_clean()
            lot.save()
            move_stock(
                balance,
                lot,
                "RETURN_RESTOCK",
                on_hand=receipt.quantity,
                inspection=-receipt.quantity,
                reason=reason,
                reference=order.pk,
            )
            sku = SKU.objects.get(pk=balance.sku_id)
            sku.requires_explicit_lot_selection = True
            sku.version += 1
            sku.save()
            receipt.restocked_lot = lot
            order.recovered_cost_fen += receipt.quantity * (
                reservation.unit_cost_fen + reservation.unit_freight_fen
            )
        else:
            move_stock(
                balance,
                old_lot,
                "RETURN_SCRAP",
                inspection=-receipt.quantity,
                reason=reason,
                reference=order.pk,
            )
        receipt.status = result
        receipt.save()
        refresh_profit(order, "return.inspected")
        event(order, actor, "return.inspected", reason, request_id)
        return {"order_id": str(order.pk)}

    return execute_once(
        f"return.inspect:{actor.pk}",
        submission_key,
        {"return_id": str(return_id), "result": result, "reason": reason, **condition},
        action,
    )
