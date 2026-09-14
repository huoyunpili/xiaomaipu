from django.db import IntegrityError, transaction

from app.accounts.policies import require_operator
from app.catalog.models import SKU
from app.common.business import BusinessError, record_event, whole
from app.common.services import execute_once
from app.finance.models import ProfitSnapshot
from app.inventory.models import InventoryBalance, StockLot
from app.inventory.services import move_stock
from app.shops.models import SalesChannel

from .models import OrderEvent, OrderItem, Reservation, SalesOrder, Shipment


def refresh_profit(order, trigger):
    order.realized_profit_fen = None
    if (
        order.status == SalesOrder.Status.COMPLETED
        and order.net_received_fen == order.adjusted_due_fen
    ):
        order.realized_profit_fen = (
            order.net_received_fen - order.cost_fen - order.fees_fen + order.recovered_cost_fen
        )
    order.version += 1
    order.save()
    ProfitSnapshot.objects.create(
        order=order,
        net_received_fen=order.net_received_fen,
        due_fen=order.adjusted_due_fen,
        cost_fen=order.cost_fen,
        fees_fen=order.fees_fen,
        recovered_cost_fen=order.recovered_cost_fen,
        realized_profit_fen=order.realized_profit_fen,
        trigger=trigger,
    )


def event(order, actor, action, description, request_id=""):
    OrderEvent.objects.create(order=order, action=action, description=description)
    record_event(actor, action, order, request_id)


def create_order(
    *,
    actor,
    submission_key,
    sku_id,
    quantity,
    unit_price_fen,
    channel_id,
    customer_name,
    selected_lot_id=None,
    customer_id=None,
    external_order_no="",
    request_id="",
):
    require_operator(actor)
    whole(quantity, "销售数量", 1)
    whole(unit_price_fen, "成交单价")
    whole(quantity * unit_price_fen, "成交金额")
    if not customer_name.strip() or len(customer_name) > 100:
        raise BusinessError("请填写客户称呼或临时标识（100 字以内）。")

    def action():
        InventoryBalance.objects.select_for_update().get(sku_id=sku_id)
        sku = SKU.objects.select_related("product").get(pk=sku_id, is_active=True)
        channel = SalesChannel.objects.get(pk=channel_id, is_active=True)
        lot = None
        if selected_lot_id:
            lot = StockLot.objects.filter(pk=selected_lot_id, sku=sku).first()
            if lot is None:
                raise BusinessError("选择的实物不属于该商品。")
        if sku.requires_explicit_lot_selection and lot is None:
            raise BusinessError("该商品存在不同货况，请选择实际出售的这件/这组货。")
        try:
            with transaction.atomic():
                order = SalesOrder.objects.create(
                    customer_id=customer_id,
                    channel=channel,
                    customer_name=customer_name.strip(),
                    amount_fen=quantity * unit_price_fen,
                    external_order_no=external_order_no.strip(),
                )
        except IntegrityError as exc:
            raise BusinessError("这个渠道订单号已录入，请查看已有订单。") from exc
        OrderItem.objects.create(
            order=order,
            sku=sku,
            selected_lot=lot,
            quantity=quantity,
            unit_price_fen=unit_price_fen,
            title_snapshot=sku.product.name,
            specification_snapshot=sku.specification,
            condition_snapshot=(lot or sku).condition_snapshot(),
        )
        event(order, actor, "order.created", "创建草稿，尚未锁定库存。", request_id)
        return {"order_id": str(order.pk)}

    return execute_once(
        f"order.create:{actor.pk}",
        submission_key,
        {
            "sku_id": str(sku_id),
            "quantity": quantity,
            "unit_price_fen": unit_price_fen,
            "channel_id": str(channel_id),
            "customer_name": customer_name,
            "selected_lot_id": str(selected_lot_id or ""),
            "customer_id": str(customer_id or ""),
            "external_order_no": external_order_no,
        },
        action,
    )


def allocate(order):
    for item in order.items.order_by("sku_id", "id"):
        balance = InventoryBalance.objects.select_for_update().get(sku_id=item.sku_id)
        sku = SKU.objects.get(pk=item.sku_id)
        remaining = item.shortage_qty
        if not remaining:
            continue
        if sku.requires_explicit_lot_selection and not item.selected_lot_id:
            raise BusinessError("商品新增了不同货况，请重新按实际货物开单，系统不会自动换货。")
        lots = StockLot.objects.filter(sku=sku, on_hand_qty__gt=0)
        if item.selected_lot_id:
            lots = lots.filter(pk=item.selected_lot_id)
        for lot in lots:
            if not lot.available_qty:
                continue
            if lot.condition_snapshot() != item.condition_snapshot:
                raise BusinessError("当前实物货况与开单时不一致，请核对后重新开单。")
            quantity = min(remaining, lot.available_qty)
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
                reason="订单锁库存",
                reference=order.pk,
            )
            item.reserved_qty += quantity
            remaining -= quantity
            if not remaining:
                break
        item.save()


def order_action(
    *,
    actor,
    submission_key,
    order_id,
    action_name,
    version,
    reason="",
    delivery_method="",
    carrier="",
    tracking_no="",
    fulfillment_fee_fen=0,
    quantity=None,
    reduction_fen=0,
    request_id="",
):
    require_operator(actor)
    whole(fulfillment_fee_fen, "实际履约费用")

    def action():
        order = SalesOrder.objects.select_for_update().select_related("channel").get(pk=order_id)
        if order.version != version:
            raise BusinessError("订单已变化，请刷新后操作。")
        if action_name == "confirm":
            if order.status not in (
                SalesOrder.Status.DRAFT,
                SalesOrder.Status.CONFIRMED,
                SalesOrder.Status.PARTIAL,
            ):
                raise BusinessError("当前订单不能确认或补锁库存。")
            if order.channel.code == "XIANYU" and reason != "已核对买家付款":
                raise BusinessError("闲鱼订单须先人工核对买家已付款，确认后仍为平台托管。")
            order.platform_paid = order.channel.code == "XIANYU"
            allocate(order)
            order.status = (
                SalesOrder.Status.PARTIAL
                if order.items.filter(shipped_qty__gt=0).exists()
                else SalesOrder.Status.CONFIRMED
            )
            description = "已确认并分配现有库存；缺口保留为缺货。"
        elif action_name == "cancel":
            if order.items.filter(shipped_qty__gt=0).exists():
                raise BusinessError("已有部分发货，不能整单取消，请处理已发货部分的售后。")
            if order.status not in (
                SalesOrder.Status.DRAFT,
                SalesOrder.Status.CONFIRMED,
                SalesOrder.Status.PARTIAL,
            ):
                raise BusinessError("已出库订单不能直接取消回补，请走退货流程。")
            if not reason.strip():
                raise BusinessError("请填写取消原因。")
            for item in order.items.order_by("sku_id", "id"):
                balance = InventoryBalance.objects.select_for_update().get(sku_id=item.sku_id)
                for reservation in item.reservations.filter(status="ACTIVE"):
                    assert reservation.lot_id is not None
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
                item.reserved_qty = 0
                item.save()
            order.status = SalesOrder.Status.CANCELLED
            order.amount_reduction_fen = order.amount_fen
            description = "已取消并释放未出库库存；已收款需单独登记退款。"
        elif action_name == "cancel_remaining":
            from .cancellation import cancel_remaining_locked

            description = cancel_remaining_locked(order, reduction_fen=reduction_fen, reason=reason)
        elif action_name == "ship":
            from .shipping import dispatch_locked_order

            description = dispatch_locked_order(
                order,
                quantity=quantity,
                delivery_method=delivery_method,
                carrier=carrier,
                tracking_no=tracking_no,
                fee_fen=fulfillment_fee_fen,
            )
        elif action_name == "complete":
            if order.status != SalesOrder.Status.SHIPPED:
                raise BusinessError("仅已发货订单可以确认履约完成。")
            if order.channel.code == "XIANYU" and reason != "已核对平台交易成功":
                raise BusinessError("闲鱼订单须人工核对平台交易成功。")
            Shipment.objects.filter(order=order).update(completed=True)
            order.status = SalesOrder.Status.COMPLETED
            description = "已确认履约完成，实际回款单独核对。"
        else:
            raise BusinessError("不支持的订单操作。")
        refresh_profit(order, f"order.{action_name}")
        event(order, actor, f"order.{action_name}", description, request_id)
        return {"order_id": str(order.pk)}

    return execute_once(
        f"order.action:{actor.pk}",
        submission_key,
        {
            "order_id": str(order_id),
            "action_name": action_name,
            "version": version,
            "reason": reason,
            "delivery_method": delivery_method,
            "carrier": carrier,
            "tracking_no": tracking_no,
            "fulfillment_fee_fen": fulfillment_fee_fen,
            "quantity": quantity,
            "reduction_fen": reduction_fen,
        },
        action,
    )
