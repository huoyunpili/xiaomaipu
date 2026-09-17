from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.workbench.models import Trade
from app.workbench.templatetags.workbench import unit_sale_price
from tests.test_workspace import connection as workspace_connection
from tests.test_workspace import make_trade

connection = workspace_connection
pytestmark = pytest.mark.django_db


def test_cost_card_uses_latest_paid_order_and_shows_unit_price(connection, admin_user, client):
    now = timezone.now()
    old = make_trade(connection, number="10001", pay_amount=10000)
    latest = make_trade(connection, number="10002", pay_amount=9999)
    unpaid = make_trade(connection, number="10003", pay_amount=90000)
    Trade.objects.filter(pk=old.pk).update(paid_at=now - timedelta(days=1))
    Trade.objects.filter(pk=latest.pk).update(paid_at=now, quantity=2)
    Trade.objects.filter(pk=unpaid.pk).update(paid_at=None, status="UNPAID")
    client.force_login(admin_user)
    response = client.get(reverse("wb-costs"))
    product = list(response.context["products"])[0]
    assert product.sale_trade_id == latest.pk
    assert product.sale_paid_fen == 9999 and product.sale_quantity == 2
    html = response.content.decode()
    assert "¥50.00 / 件" in html
    assert "2 件共 ¥99.99" in html
    assert unit_sale_price(0, 1) == "0.00"
