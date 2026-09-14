from django.db.models import Sum

from app.finance.models import CustomerPaymentFact
from app.integrations.models import PlatformOrder
from app.inventory.models import InventoryBalance, StockLot
from app.orders.models import OrderItem, SalesOrder
from app.procurement.models import Purchase, PurchaseDispatch


def financial_summary():
    operating_orders = list(
        SalesOrder.objects.exclude(
            status__in=[SalesOrder.Status.DRAFT, SalesOrder.Status.CANCELLED]
        ).only(
            "amount_fen",
            "amount_reduction_fen",
            "received_fen",
            "refunded_fen",
            "realized_profit_fen",
        )
    )
    purchases = list(
        Purchase.objects.only(
            "quantity",
            "cancelled_qty",
            "returned_qty",
            "rejected_returned_qty",
            "unit_cost_fen",
            "paid_fen",
            "refunded_fen",
        )
    )
    customer_payment_fen = (
        CustomerPaymentFact.objects.exclude(
            order__status__in=[SalesOrder.Status.DRAFT, SalesOrder.Status.CANCELLED]
        ).aggregate(total=Sum("amount_fen"))["total"]
        or 0
    )
    pending_platform_snapshots = (
        PlatformOrder.objects.exclude(scope_status=PlatformOrder.Scope.HISTORICAL)
        .filter(needs_review=True)
        .values_list("snapshot", flat=True)
    )
    platform_pending_fen = sum(
        snapshot.get("pay_amount", 0)
        for snapshot in pending_platform_snapshots
        if type(snapshot.get("pay_amount")) is int
    )
    return {
        "cash_received_fen": sum(order.net_received_fen for order in operating_orders),
        "customer_payment_fen": customer_payment_fen,
        "receivable_fen": sum(order.outstanding_fen for order in operating_orders),
        "realized_profit_fen": sum(order.realized_profit_fen or 0 for order in operating_orders),
        "supplier_paid_fen": sum(purchase.net_paid_fen for purchase in purchases),
        "supplier_payable_fen": sum(purchase.payable_fen for purchase in purchases),
        "supplier_refund_fen": sum(purchase.refund_due_fen for purchase in purchases),
        "platform_pending_fen": platform_pending_fen,
    }


def goods_summary():
    inventory = InventoryBalance.objects.aggregate(
        on_hand=Sum("on_hand_qty"), reserved=Sum("reserved_qty"), inspection=Sum("inspection_qty")
    )
    on_hand = inventory["on_hand"] or 0
    reserved = inventory["reserved"] or 0
    in_transit = sum(
        quantity - received - disposed
        for quantity, received, disposed in PurchaseDispatch.objects.values_list(
            "quantity", "received_qty", "disposed_qty"
        )
    )
    pending_customer = sum(
        quantity - shipped - cancelled
        for quantity, shipped, cancelled in OrderItem.objects.filter(
            order__status__in=[SalesOrder.Status.CONFIRMED, SalesOrder.Status.PARTIAL]
        ).values_list("quantity", "shipped_qty", "cancelled_qty")
    )
    stock_cost_fen = sum(
        lot.on_hand_qty * lot.purchase_cost_fen
        for lot in StockLot.objects.only("on_hand_qty", "unit_cost_fen", "unit_freight_fen")
    )
    pending_platform_snapshots = (
        PlatformOrder.objects.exclude(scope_status=PlatformOrder.Scope.HISTORICAL)
        .filter(needs_review=True)
        .values_list("snapshot", flat=True)
    )
    platform_pending_qty = 0
    for snapshot in pending_platform_snapshots:
        goods = snapshot.get("goods")
        if isinstance(goods, dict) and type(goods.get("quantity")) is int:
            platform_pending_qty += goods["quantity"]
    return {
        "on_hand_qty": on_hand,
        "available_qty": on_hand - reserved,
        "reserved_qty": reserved,
        "inspection_qty": inventory["inspection"] or 0,
        "in_transit_qty": in_transit,
        "pending_customer_qty": pending_customer,
        "stock_cost_fen": stock_cost_fen,
        "platform_pending_qty": platform_pending_qty,
    }
