import pytest
from django.urls import reverse

from app.integrations.services import store_order
from app.workbench.models import ProductCost, Trade
from tests.test_workspace import connection as workspace_connection
from tests.test_workspace import payload

connection = workspace_connection
pytestmark = pytest.mark.django_db


def test_manual_condition_is_saved_displayed_and_not_replaced_by_sync(
    connection, admin_user, client
):
    data = payload()
    data["goods"]["sku_text"] = "款式:160w pro"
    row = store_order(connection, data)
    trade = Trade.objects.get(platform=row)
    product = trade.product
    assert product.display_condition == ""
    client.force_login(admin_user)
    response = client.post(
        reverse("wb-costs"),
        {"product": str(product.pk), "condition": "99新", "cost": "", "supplier": ""},
        HTTP_ACCEPT="application/json",
    )
    assert response.status_code == 200
    assert response.json()["saved"]["condition"] == "99新"
    assert response.json()["display_condition"] == "99新"
    store_order(connection, {**data, "update_time": data["update_time"] + 1})
    product.refresh_from_db()
    assert product.condition == "99新"
    assert product.spec == "款式:160w pro"
    assert product.unit_fen is None and product.version == 1
    assert ProductCost.objects.count() == 1
    for url in (reverse("wb-costs"), reverse("wb-detail", args=[trade.pk])):
        html = client.get(url).content.decode()
        assert "99新" in html
        assert data["goods"]["title"] in html
        assert "款式:160w pro" in html


def test_condition_is_taken_only_from_explicit_condition_spec():
    assert ProductCost(spec="款式:160w pro").display_condition == ""
    assert ProductCost(spec="成色:全新仅拆封;款式:160w pro").display_condition == "全新仅拆封"
    assert ProductCost(spec="款式:160w pro；成色：99新").display_condition == "99新"
    assert ProductCost(spec="成色:99新", condition="95新").display_condition == "95新"
