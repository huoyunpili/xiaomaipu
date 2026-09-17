from decimal import ROUND_HALF_UP, Decimal

from django import template

register = template.Library()


@register.filter
def money(value):
    return "待补成本" if value is None else f"{Decimal(value) / 100:,.2f}"


@register.filter
def money_input(value):
    return "" if value is None else f"{Decimal(value) / 100:.2f}"


@register.filter
def unit_sale_price(paid_fen, quantity):
    if paid_fen is None or not quantity:
        return "暂无成交记录"
    value = (Decimal(paid_fen) / Decimal(quantity) / 100).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return f"{value:,.2f}"
