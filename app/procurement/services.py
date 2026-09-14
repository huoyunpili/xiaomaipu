from app.accounts.policies import require_operator
from app.catalog.models import CONDITION_FIELDS, SKU
from app.common.business import BusinessError, record_event, whole
from app.common.services import execute_once
from app.finance.models import MoneyEntry
from app.inventory.models import InventoryBalance, StockLot
from app.inventory.services import move_stock
from app.orders.models import OrderItem, SalesOrder

from .models import Purchase, PurchaseEvent, PurchaseReceipt, Supplier, SupplierQuote


def save_supplier(
    *,
    actor,
    submission_key,
    name,
    contact="",
    notes="",
    supplier_id=None,
    version=None,
    request_id="",
):
    require_operator(actor)

    def action():
        supplier = (
            Supplier.objects.select_for_update().get(pk=supplier_id) if supplier_id else Supplier()
        )
        if supplier_id and supplier.version != version:
            raise BusinessError("供应商已被修改，请刷新后再保存。")
        supplier.name, supplier.contact, supplier.notes = (
            name.strip(),
            contact.strip(),
            notes.strip(),
        )
        supplier.version += 1
        supplier.full_clean()
        supplier.save()
        record_event(actor, "supplier.saved", supplier, request_id)
        return {"supplier_id": str(supplier.pk)}

    return execute_once(
        f"supplier.save:{actor.pk}",
        submission_key,
        dict(
            supplier_id=str(supplier_id or ""),
            version=version,
            name=name,
            contact=contact,
            notes=notes,
        ),
        action,
    )


def add_quote(
    *, actor, submission_key, supplier_id, sku_id, unit_cost_fen, request_id="", **condition
):
    require_operator(actor)
    whole(unit_cost_fen, "单件进货成本")

    def action():
        quote = SupplierQuote(
            supplier_id=supplier_id,
            sku_id=sku_id,
            unit_cost_fen=unit_cost_fen,
            **{field: condition.get(field, "") for field in CONDITION_FIELDS},
        )
        quote.full_clean()
        quote.save()
        record_event(actor, "supplier.quote_added", quote, request_id)
        return {"supplier_id": str(supplier_id), "quote_id": str(quote.pk)}

    return execute_once(
        f"quote.add:{actor.pk}",
        submission_key,
        dict(
            supplier_id=str(supplier_id),
            sku_id=str(sku_id),
            unit_cost_fen=unit_cost_fen,
            **condition,
        ),
        action,
    )


def create_purchase(
    *,
    actor,
    submission_key,
    supplier_id,
    sku_id,
    quantity,
    unit_cost_fen,
    quote_id=None,
    order_item_id=None,
    direct=False,
    request_id="",
    **condition,
):
    require_operator(actor)
    whole(quantity, "采购数量", 1)
    whole(unit_cost_fen, "单件进货成本")

    def action():
        supplier = Supplier.objects.get(pk=supplier_id)
        if direct and not order_item_id:
            raise BusinessError("直发采购必须从对应销售订单的缺货入口创建。")
        if order_item_id:
            original = OrderItem.objects.get(pk=order_item_id)
            order = SalesOrder.objects.select_for_update().get(pk=original.order_id)
            item = OrderItem.objects.get(pk=order_item_id)
            InventoryBalance.objects.select_for_update().get(sku_id=item.sku_id)
            pending = sum(p.planned_qty for p in Purchase.objects.filter(order_item=item))
            if order.status not in (SalesOrder.Status.CONFIRMED, SalesOrder.Status.PARTIAL) or str(
                item.sku_id
            ) != str(sku_id):
                raise BusinessError("只能为已确认订单采购对应商品。")
            if quantity > max(item.shortage_qty - pending, 0):
                raise BusinessError("采购数量超过尚未安排采购的缺口，请先查看已关联采购。")
        sku = SKU.objects.select_related("product").get(pk=sku_id, is_active=True)
        if (
            quote_id
            and not SupplierQuote.objects.filter(
                pk=quote_id, supplier_id=supplier_id, sku_id=sku_id
            ).exists()
        ):
            raise BusinessError("报价与供应商或商品不匹配，请重新选择。")
        purchase = Purchase(
            direct=direct,
            order_item_id=order_item_id,
            supplier=supplier,
            supplier_name=supplier.name,
            sku=sku,
            product_name=sku.product.name,
            quote_id=quote_id,
            quantity=quantity,
            unit_cost_fen=unit_cost_fen,
            **{field: condition.get(field, "") for field in CONDITION_FIELDS},
        )
        purchase.full_clean()
        purchase.save()
        record_event(actor, "purchase.created", purchase, request_id, quantity=quantity)
        return {"purchase_id": str(purchase.pk)}

    return execute_once(
        f"purchase.create:{actor.pk}",
        submission_key,
        dict(
            supplier_id=str(supplier_id),
            sku_id=str(sku_id),
            quote_id=str(quote_id or ""),
            order_item_id=str(order_item_id or ""),
            direct=direct,
            quantity=quantity,
            unit_cost_fen=unit_cost_fen,
            **condition,
        ),
        action,
    )


def purchase_action(
    *,
    actor,
    submission_key,
    purchase_id,
    version,
    operation,
    quantity=0,
    amount_fen=0,
    receipt_id=None,
    reason="",
    request_id="",
    **condition,
):
    if operation in {"ship", "receive"}:
        from .logistics import logistics_action

        return logistics_action(
            actor=actor,
            submission_key=submission_key,
            purchase_id=purchase_id,
            version=version,
            operation=operation,
            quantity=quantity,
            reason=reason,
            request_id=request_id,
            **condition,
        )
    require_operator(actor)
    whole(quantity, "数量")
    whole(amount_fen, "金额")
    if not reason.strip():
        raise BusinessError("请填写操作说明。")

    def action():
        original = Purchase.objects.select_related("order_item").get(pk=purchase_id)
        if original.order_item_id:
            assert original.order_item is not None
            SalesOrder.objects.select_for_update().get(pk=original.order_item.order_id)
        purchase = Purchase.objects.select_for_update().get(pk=purchase_id)
        event_receipt = None
        if purchase.version != version:
            raise BusinessError("采购单已变化，请刷新后再操作。")
        if operation == "order":
            if purchase.closed or purchase.ordered:
                raise BusinessError("当前采购单不能再次下单。")
            purchase.ordered = True
        elif operation == "return":
            whole(quantity, "退回数量", 1)
            receipt = PurchaseReceipt.objects.filter(pk=receipt_id, purchase=purchase).first()
            if not receipt:
                raise BusinessError("请选择这张采购单的收货记录。")
            balance = InventoryBalance.objects.select_for_update().get(sku_id=purchase.sku_id)
            lot = StockLot.objects.get(pk=receipt.lot_id)
            if quantity > min(receipt.quantity - receipt.returned_qty, lot.available_qty):
                raise BusinessError(
                    "退回数量超过这次收货剩余可退库存，已售出或被订单占用的货不能退回。"
                )
            move_stock(
                balance,
                lot,
                "PURCHASE_RETURN",
                on_hand=-quantity,
                reason=reason,
                reference=purchase.pk,
            )
            receipt.returned_qty += quantity
            receipt.save()
            purchase.returned_qty += quantity
            event_receipt = receipt
        elif operation == "close":
            if purchase.legacy_logistics or purchase.unmatched_qty:
                raise BusinessError("请先核对历史发运或未匹配到货，再关闭未发部分。")
            if purchase.closed or not purchase.unshipped_qty:
                raise BusinessError("采购单没有可关闭的剩余数量。")
            purchase.cancelled_qty += purchase.unshipped_qty
            purchase.closed = True
        elif operation == "pay":
            whole(amount_fen, "付款金额", 1)
            if not purchase.ordered or amount_fen > purchase.payable_fen:
                raise BusinessError("请先确认下单，付款不能超过剩余应付金额。")
            purchase.paid_fen += amount_fen
        elif operation == "refund":
            whole(amount_fen, "退款金额", 1)
            if amount_fen > purchase.net_paid_fen:
                raise BusinessError("供应商退款不能超过已付净额。")
            purchase.refunded_fen += amount_fen
        else:
            raise BusinessError("不支持的采购操作。")
        purchase.version += 1
        purchase.full_clean()
        purchase.save()
        event = PurchaseEvent(
            purchase=purchase,
            kind=operation,
            quantity=quantity,
            amount_fen=amount_fen,
            reason=reason,
            receipt=event_receipt,
        )
        event.full_clean()
        event.save()
        if operation in {"pay", "refund"}:
            MoneyEntry.objects.create(
                purchase=purchase,
                purchase_event=event,
                actor=actor,
                kind="PAYMENT" if operation == "pay" else "REFUND",
                direction="OUT" if operation == "pay" else "IN",
                amount_fen=amount_fen,
                reason=reason,
            )
        record_event(
            actor,
            "purchase." + operation,
            purchase,
            request_id,
            quantity=quantity,
            amount_fen=amount_fen,
            reason=reason,
        )
        return {"purchase_id": str(purchase.pk)}

    return execute_once(
        f"purchase.action:{actor.pk}",
        submission_key,
        dict(
            purchase_id=str(purchase_id),
            version=version,
            operation=operation,
            quantity=quantity,
            amount_fen=amount_fen,
            reason=reason,
            receipt_id=str(receipt_id or ""),
            **condition,
        ),
        action,
    )
