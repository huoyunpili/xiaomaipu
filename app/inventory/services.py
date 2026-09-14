from app.accounts.policies import require_admin, require_operator
from app.catalog.models import CONDITION_FIELDS, SKU
from app.common.business import BusinessError, record_event, whole
from app.common.services import execute_once

from .models import InventoryBalance, StockLot, StockMovement


def move_stock(balance, lot, kind, *, on_hand=0, reserved=0, inspection=0, reason, reference=""):
    """Caller holds the SKU balance lock; each delta shares the caller's transaction."""
    balance.on_hand_qty += on_hand
    balance.reserved_qty += reserved
    balance.inspection_qty += inspection
    lot.on_hand_qty += on_hand
    lot.reserved_qty += reserved
    if (
        min(
            balance.available_qty,
            balance.reserved_qty,
            balance.inspection_qty,
            lot.available_qty,
            lot.reserved_qty,
        )
        < 0
    ):
        raise BusinessError("库存不足或已被订单占用，不能执行该操作。")
    balance.save()
    lot.version += 1
    lot.save()
    StockMovement.objects.create(
        sku_id=balance.sku_id,
        lot=lot,
        kind=kind,
        on_hand_delta=on_hand,
        reserved_delta=reserved,
        inspection_delta=inspection,
        on_hand_after=balance.on_hand_qty,
        reserved_after=balance.reserved_qty,
        reason=reason,
        reference_id=str(reference),
    )


def receive_stock(
    *,
    actor,
    submission_key,
    sku_id,
    quantity,
    unit_cost_fen,
    unit_freight_fen=0,
    label="",
    supplier_name="",
    reason="采购/期初入库",
    explicit_selection=False,
    request_id="",
    **condition,
):
    require_operator(actor)
    whole(quantity, "入库数量", 1)
    whole(unit_cost_fen, "单件采购成本")
    whole(unit_freight_fen, "单件采购运费")
    if not reason.strip():
        raise BusinessError("请填写入库原因。")
    payload = dict(
        sku_id=str(sku_id),
        quantity=quantity,
        unit_cost_fen=unit_cost_fen,
        unit_freight_fen=unit_freight_fen,
        label=label,
        supplier_name=supplier_name,
        reason=reason,
        explicit_selection=explicit_selection,
        **condition,
    )

    def action():
        balance = InventoryBalance.objects.select_for_update().get(sku_id=sku_id)
        sku = SKU.objects.get(pk=sku_id, is_active=True)
        descriptions = {
            field: condition.get(field, getattr(sku, field)) for field in CONDITION_FIELDS
        }
        lot = StockLot(
            sku=sku,
            label=label,
            supplier_name=supplier_name,
            unit_cost_fen=unit_cost_fen,
            unit_freight_fen=unit_freight_fen,
            received_qty=quantity,
            on_hand_qty=0,
            **descriptions,
        )
        lot.full_clean()
        lot.save()
        if (
            explicit_selection
            or lot.condition_snapshot() != sku.condition_snapshot()
            or any(
                other.condition_snapshot() != lot.condition_snapshot()
                for other in sku.lots.filter(on_hand_qty__gt=0)
            )
        ):
            sku.requires_explicit_lot_selection = True
            sku.version += 1
            sku.save()
        move_stock(balance, lot, "RECEIPT", on_hand=quantity, reason=reason)
        record_event(actor, "stock.received", lot, request_id, quantity=quantity)
        return {"lot_id": str(lot.pk), "sku_id": str(sku.pk)}

    return execute_once(f"stock.receive:{actor.pk}", submission_key, payload, action)


def adjust_stock(*, actor, submission_key, lot_id, actual_qty, version, reason, request_id=""):
    require_admin(actor)
    whole(actual_qty, "实盘数量")
    if not reason.strip():
        raise BusinessError("请填写盘点调整原因。")

    def action():
        original = StockLot.objects.get(pk=lot_id)
        balance = InventoryBalance.objects.select_for_update().get(sku_id=original.sku_id)
        lot = StockLot.objects.get(pk=lot_id)
        if lot.version != version:
            raise BusinessError("库存已变化，请刷新后重新盘点。")
        if actual_qty < lot.reserved_qty:
            raise BusinessError("实盘数量少于已锁库存，请先处理占用该货的订单，不能直接盘亏。")
        delta = actual_qty - lot.on_hand_qty
        if not delta:
            raise BusinessError("实盘数量与账面相同，无需调整。")
        move_stock(
            balance, lot, "COUNT_GAIN" if delta > 0 else "COUNT_LOSS", on_hand=delta, reason=reason
        )
        record_event(actor, "stock.adjusted", lot, request_id, delta=delta)
        return {"lot_id": str(lot.pk)}

    return execute_once(
        f"stock.adjust:{actor.pk}",
        submission_key,
        {"lot_id": str(lot_id), "actual_qty": actual_qty, "version": version, "reason": reason},
        action,
    )


def edit_lot(*, actor, submission_key, lot_id, version, label="", request_id="", **condition):
    require_operator(actor)

    def action():
        original = StockLot.objects.get(pk=lot_id)
        InventoryBalance.objects.select_for_update().get(sku_id=original.sku_id)
        lot = StockLot.objects.get(pk=lot_id)
        if lot.version != version or lot.reserved_qty:
            raise BusinessError(
                "货物已变化或已被订单锁定，请先核对相关订单；不能直接改动已备货的描述。"
            )
        for field in CONDITION_FIELDS:
            setattr(lot, field, condition.get(field, ""))
        lot.label = label
        lot.version += 1
        lot.full_clean()
        lot.save()
        sku = SKU.objects.get(pk=lot.sku_id)
        sku.requires_explicit_lot_selection = True
        sku.version += 1
        sku.save()
        record_event(actor, "stock.description_updated", lot, request_id)
        return {"sku_id": str(lot.sku_id)}

    return execute_once(
        f"stock.edit:{actor.pk}",
        submission_key,
        {"lot_id": str(lot_id), "version": version, "label": label, **condition},
        action,
    )
