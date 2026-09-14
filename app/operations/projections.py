from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from app.inventory.models import StockLot
from app.orders.models import SalesOrder
from app.procurement.models import Purchase, PurchaseDispatch

from .models import BottleneckThreshold

KIND_LABELS = {
    "K1": "已付供应商，仍待发",
    "K2": "采购在途已超预计到货",
    "K3": "长期未售库存",
    "K4": "客户已付款，仍待发",
    "K5": "已交付，钱未结清",
    "K6": "售后钱货不同步",
    "K7": "货已收到，仍欠供应商",
}
DEFAULT_THRESHOLDS = {"K1": 0, "K2": 0, "K3": 90, "K4": 0, "K5": 0, "K7": 0}


def threshold_days():
    values = DEFAULT_THRESHOLDS.copy()
    values.update(dict(BottleneckThreshold.objects.values_list("kind", "days")))
    return values


def _age_days(start, as_of):
    if start is None:
        return None
    return max((as_of - start).days, 0)


def _card(
    kind,
    object_type,
    obj,
    *,
    title,
    detail,
    quantity=None,
    amount_fen=None,
    wait_start=None,
    due_at=None,
    unknown_reason="",
    url="",
):
    return {
        "kind": kind,
        "kind_label": KIND_LABELS[kind],
        "object_type": object_type,
        "object_id": obj.pk,
        "title": title,
        "detail": detail,
        "quantity": quantity,
        "amount_fen": amount_fen,
        "wait_start": wait_start,
        "due_at": due_at,
        "age_days": None,
        "unknown_reason": unknown_reason,
        "url": url,
    }


def purchase_progress(purchase_ids=None, as_of=None):
    as_of = as_of or timezone.now()
    rows = Purchase.objects.select_related("supplier", "sku__product").prefetch_related(
        "dispatches", "arrivals", "receipts__lot", "money_entries"
    )
    if purchase_ids is not None:
        rows = rows.filter(pk__in=purchase_ids)
    result = []
    for purchase in rows:
        result.append(
            {
                "object_type": "purchase",
                "id": purchase.pk,
                "number": purchase.number,
                "title": purchase.product_name,
                "unshipped_qty": purchase.unshipped_qty,
                "in_transit_qty": purchase.in_transit_qty,
                "arrived_qty": purchase.arrived_qty,
                "awaiting_inspection_qty": purchase.awaiting_inspection_qty,
                "accepted_qty": purchase.received_qty,
                "payable_fen": purchase.payable_fen,
                "refund_due_fen": purchase.refund_due_fen,
                "legacy_logistics": purchase.legacy_logistics,
                "unmatched_qty": purchase.unmatched_qty,
                "as_of": as_of,
            }
        )
    return result


def order_progress(order_ids=None, as_of=None):
    as_of = as_of or timezone.now()
    rows = SalesOrder.objects.select_related("channel").prefetch_related(
        "items", "shipments", "customer_payments", "money_entries"
    )
    if order_ids is not None:
        rows = rows.filter(pk__in=order_ids)
    result = []
    for order in rows:
        demand = sum(item.quantity - item.cancelled_qty for item in order.items.all())
        shipped = sum(item.shipped_qty for item in order.items.all())
        reserved = sum(item.reserved_qty for item in order.items.all())
        result.append(
            {
                "object_type": "order",
                "id": order.pk,
                "number": order.number,
                "title": order.customer_name,
                "demand_qty": demand,
                "reserved_qty": reserved,
                "shipped_qty": shipped,
                "unshipped_qty": max(demand - shipped, 0),
                "due_fen": order.adjusted_due_fen,
                "cash_received_fen": order.net_received_fen,
                "outstanding_fen": order.outstanding_fen,
                "platform_payment_known": bool(order.customer_payments.all()),
                "as_of": as_of,
            }
        )
    return result


def bottlenecks(kind="", as_of=None):
    as_of = as_of or timezone.now()
    thresholds = threshold_days()
    cards = []

    purchases = Purchase.objects.select_related("supplier", "sku__product").prefetch_related(
        "dispatches", "arrivals", "receipts__lot", "money_entries"
    )
    for purchase in purchases:
        payments = [entry for entry in purchase.money_entries.all() if entry.kind == "PAYMENT"]
        if purchase.net_paid_fen > 0 and purchase.unshipped_qty > 0:
            known = [entry.occurred_at for entry in payments if entry.occurred_at]
            start = min(known) if known else None
            due = start + timedelta(days=thresholds["K1"]) if start else None
            cards.append(
                _card(
                    "K1",
                    "purchase",
                    purchase,
                    title=f"{purchase.number} · {purchase.product_name}",
                    detail=f"供应商 {purchase.supplier_name}，仍待发 {purchase.unshipped_qty} 件",
                    quantity=purchase.unshipped_qty,
                    amount_fen=purchase.net_paid_fen,
                    wait_start=start,
                    due_at=due,
                    unknown_reason="付款业务时间未记录" if not start else "",
                    url=reverse("purchase-detail", args=[purchase.pk]),
                )
            )
        if purchase.payable_fen > 0 and purchase.arrived_qty > 0:
            dates = [
                arrival.received_at for arrival in purchase.arrivals.all() if arrival.received_at
            ]
            start = min(dates) if dates else None
            due = start + timedelta(days=thresholds["K7"]) if start else None
            cards.append(
                _card(
                    "K7",
                    "purchase",
                    purchase,
                    title=f"{purchase.number} · {purchase.product_name}",
                    detail=f"实际已收到 {purchase.arrived_qty} 件，仍欠供应商",
                    quantity=purchase.arrived_qty,
                    amount_fen=purchase.payable_fen,
                    wait_start=start,
                    due_at=due,
                    unknown_reason="实际收货时间未记录" if not start else "",
                    url=reverse("purchase-detail", args=[purchase.pk]),
                )
            )

    for dispatch in PurchaseDispatch.objects.select_related("purchase").filter(
        expected_arrival_at__lt=as_of
    ):
        if dispatch.remaining_qty > 0:
            cards.append(
                _card(
                    "K2",
                    "dispatch",
                    dispatch,
                    title=f"{dispatch.purchase.number} · 第 {str(dispatch.pk)[:8]} 批",
                    detail=f"超过预计到货，仍在途 {dispatch.remaining_qty} 件",
                    quantity=dispatch.remaining_qty,
                    wait_start=dispatch.dispatched_at,
                    due_at=dispatch.expected_arrival_at,
                    unknown_reason="发货业务时间未记录" if not dispatch.dispatched_at else "",
                    url=reverse("purchase-detail", args=[dispatch.purchase_id]),
                )
            )

    cutoff = as_of - timedelta(days=thresholds["K3"])
    for lot in StockLot.objects.select_related("sku__product").filter(
        original_received_at__lte=cutoff, on_hand_qty__gt=0
    ):
        if lot.available_qty > 0:
            original_received_at = lot.original_received_at
            assert original_received_at is not None
            cards.append(
                _card(
                    "K3",
                    "lot",
                    lot,
                    title=f"{lot.sku.product.name} · {lot.label or str(lot.pk)[:8]}",
                    detail=f"可售 {lot.available_qty} 件，按原始入库时间计算库龄",
                    quantity=lot.available_qty,
                    amount_fen=lot.available_qty * lot.purchase_cost_fen,
                    wait_start=original_received_at,
                    due_at=original_received_at + timedelta(days=thresholds["K3"]),
                    url=reverse("product-detail", args=[lot.sku_id]),
                )
            )

    orders = SalesOrder.objects.select_related("channel").prefetch_related(
        "items", "shipments", "customer_payments", "money_entries"
    )
    for order in orders:
        demand = sum(item.quantity - item.cancelled_qty for item in order.items.all())
        shipped = sum(item.shipped_qty for item in order.items.all())
        unshipped = max(demand - shipped, 0)
        payment_facts = list(order.customer_payments.all())
        if payment_facts and unshipped:
            known = [fact.occurred_at for fact in payment_facts if fact.occurred_at]
            start = min(known) if known else None
            due = start + timedelta(days=thresholds["K4"]) if start else None
            cards.append(
                _card(
                    "K4",
                    "order",
                    order,
                    title=f"{order.number} · {order.customer_name}",
                    detail=f"客户付款依据已核对，仍待发 {unshipped} 件",
                    quantity=unshipped,
                    amount_fen=sum(fact.amount_fen for fact in payment_facts),
                    wait_start=start,
                    due_at=due,
                    unknown_reason="客户付款业务时间未记录" if not start else "",
                    url=reverse("order-detail", args=[order.pk]),
                )
            )
        completed = [shipment for shipment in order.shipments.all() if shipment.completed]
        if completed and order.outstanding_fen > 0:
            cards.append(
                _card(
                    "K5",
                    "order",
                    order,
                    title=f"{order.number} · {order.customer_name}",
                    detail="已有完成交付记录，按当前实际现金口径仍未结清",
                    amount_fen=order.outstanding_fen,
                    unknown_reason="签收时间与结算分配尚未结构化",
                    url=reverse("order-detail", args=[order.pk]),
                )
            )

    for card in cards:
        card["age_days"] = _age_days(card["wait_start"], as_of)
        card["overdue"] = bool(
            card["kind"] in {"K2", "K3"} and card["due_at"] and card["due_at"] < as_of
        )
    if kind:
        cards = [card for card in cards if card["kind"] == kind]
    return sorted(
        cards, key=lambda card: (not card["overdue"], card["kind"], str(card["object_id"]))
    )


def supplemental_issues(as_of=None):
    as_of = as_of or timezone.now()
    return {
        "dispatches_without_eta": PurchaseDispatch.objects.filter(
            expected_arrival_at__isnull=True
        ).count(),
        "lots_without_original_time": StockLot.objects.filter(
            original_received_at__isnull=True, on_hand_qty__gt=0
        ).count(),
        "unmatched_arrivals": sum(
            p.unmatched_qty for p in Purchase.objects.prefetch_related("arrivals")
        ),
        "supplier_refund_due_fen": sum(
            purchase.refund_due_fen for purchase in Purchase.objects.all()
        ),
        "legacy_platform_payments_without_fact": SalesOrder.objects.filter(
            platform_paid=True, customer_payments__isnull=True
        ).count(),
        "as_of": as_of,
    }
