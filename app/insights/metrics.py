from datetime import timedelta

from django.db.models import Q, Sum
from django.utils import timezone

from app.orders.models import Reservation, SalesOrder


def product_metrics(sku):
    today = timezone.localdate()
    allocations = Reservation.objects.filter(item__sku=sku, status="CONSUMED")

    def since(days):
        cutoff = timezone.now() - timedelta(days=days)
        return allocations.filter(
            Q(shipment__created_at__gte=cutoff)
            | Q(
                shipment__isnull=True,
                item__order__events__action="order.ship",
                item__order__events__occurred_at__gte=cutoff,
            )
        ).distinct()

    last30 = since(30)
    last7 = since(7)
    qty30 = sum(max(r.quantity - r.returned_qty, 0) for r in last30)
    qty7 = sum(max(r.quantity - r.returned_qty, 0) for r in last7)
    available = sku.balance.available_qty
    costs = sum(lot.available_qty * lot.purchase_cost_fen for lot in sku.lots.all())
    price = sku.market_prices.first()
    stale = price is not None and (today - price.observed_on).days > 30
    loss = max(costs - available * price.price_fen, 0) if price and not stale else None
    events = sku.market_events.filter(
        confirmed=True, event_date__gte=today, event_date__lte=today + timedelta(days=30)
    )
    if not qty30:
        trend = "近 30 天未成交"
    elif qty7 / 7 > qty30 / 30 * 1.3:
        trend = "近期销量上升"
    elif qty7 / 7 < qty30 / 30 * 0.7:
        trend = "近期销量下降"
    else:
        trend = "近期销量平稳"
    age_days = (today - sku.created_at.date()).days
    phase = (
        "上新观察"
        if age_days < 30
        else "滞销观察"
        if not qty30
        else "需求回落观察"
        if trend == "近期销量下降"
        else "正常售卖"
    )
    return dict(
        sku=sku,
        qty30=qty30,
        qty7=qty7,
        trend=trend,
        phase=phase,
        stock_days=round(available * 30 / qty30, 1) if qty30 else None,
        cost_fen=costs,
        price=price,
        price_stale=stale,
        loss_fen=loss,
        events=events,
    )


def order_totals(orders):
    totals = orders.aggregate(
        received=Sum("received_fen"),
        refunded=Sum("refunded_fen"),
        cost=Sum("cost_fen"),
        fees=Sum("fees_fen"),
        profit=Sum("realized_profit_fen"),
    )
    return {key: value or 0 for key, value in totals.items()}


def active_orders():
    return SalesOrder.objects.exclude(status="DRAFT")
