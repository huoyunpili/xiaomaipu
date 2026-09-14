from uuid import uuid5

from app.accounts.policies import require_operator
from app.common.business import BusinessError, record_event, whole
from app.common.services import execute_once
from app.inventory.models import InventoryBalance

from .models import CONDITION_FIELDS, SKU, Product


def create_product_with_stock(
    *,
    actor,
    submission_key,
    name,
    initial_quantity=0,
    unit_cost_fen=None,
    unit_freight_fen=0,
    request_id="",
    **fields,
):
    from app.inventory.services import receive_stock

    require_operator(actor)
    whole(initial_quantity, "库存数量")
    whole(unit_freight_fen, "采购运费")
    if initial_quantity:
        whole(unit_cost_fen, "单件采购成本")

    def action():
        result = save_product(
            actor=actor,
            submission_key=uuid5(submission_key, "product"),
            name=name,
            request_id=request_id,
            **fields,
        )
        if initial_quantity:
            receive_stock(
                actor=actor,
                submission_key=uuid5(submission_key, "stock"),
                sku_id=result["sku_id"],
                quantity=initial_quantity,
                unit_cost_fen=unit_cost_fen,
                unit_freight_fen=unit_freight_fen,
                label="首次录入",
                reason="新增商品时录入库存",
                request_id=request_id,
            )
        return result

    return execute_once(
        f"product.create_with_stock:{actor.pk}",
        submission_key,
        {
            "name": name,
            "initial_quantity": initial_quantity,
            "unit_cost_fen": unit_cost_fen,
            "unit_freight_fen": unit_freight_fen,
            **fields,
        },
        action,
    )


def save_product(
    *, actor, submission_key, name, sku_id=None, version=None, request_id="", **fields
):
    require_operator(actor)
    name = name.strip()
    if not name or len(name) > 200:
        raise BusinessError("请填写 1～200 字的商品名称。")
    allowed = {key: fields.get(key, "").strip() for key in CONDITION_FIELDS}
    allowed["specification"] = fields.get("specification", "").strip()

    def action():
        if sku_id:
            InventoryBalance.objects.select_for_update().get(sku_id=sku_id)
            sku = SKU.objects.select_related("product").get(pk=sku_id)
            if sku.version != version:
                raise BusinessError("商品已被修改，请刷新后再保存。")
            sku.product.name = name
            sku.product.save(update_fields=["name", "updated_at"])
            for key, value in allowed.items():
                setattr(sku, key, value)
            sku.version += 1
            if any(
                lot.condition_snapshot() != sku.condition_snapshot()
                for lot in sku.lots.filter(on_hand_qty__gt=0)
            ):
                sku.requires_explicit_lot_selection = True
            sku.full_clean()
            sku.save()
        else:
            product = Product.objects.create(name=name)
            sku = SKU(product=product, **allowed)
            sku.full_clean()
            sku.save()
            InventoryBalance.objects.create(sku=sku)
        record_event(actor, "product.saved", sku, request_id)
        return {"sku_id": str(sku.pk)}

    return execute_once(
        f"product.save:{actor.pk}",
        submission_key,
        {"sku_id": str(sku_id or ""), "version": version, "name": name, **allowed},
        action,
    )
