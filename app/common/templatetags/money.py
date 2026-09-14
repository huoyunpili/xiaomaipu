from decimal import Decimal

from django import template

register = template.Library()


@register.filter
def yuan(value):
    if value is None or value == "":
        return "待确认"
    return f"{Decimal(value) / 100:,.2f}"
