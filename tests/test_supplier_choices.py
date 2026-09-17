import pytest
from django.urls import reverse

from app.shops.models import Shop
from app.workbench.models import ProductCost, Trade
from app.workbench.services import supplier_choices
from tests.test_workspace import connection as workspace_connection
from tests.test_workspace import make_trade

connection = workspace_connection
pytestmark = pytest.mark.django_db


def test_inline_save_json_validation_and_retries(connection, admin_user, client):
    trade = make_trade(connection)
    client.force_login(admin_user)
    url = reverse("wb-costs")
    data = {"product": trade.product_id, "cost": "-1", "supplier": "新供应商"}
    response = client.post(url, data, HTTP_ACCEPT="application/json")
    assert response.status_code == 400 and "cost" in response.json()["errors"]
    trade.product.refresh_from_db()
    assert trade.product.unit_fen is None
    data.update(cost="60", supplier_wechat="新微信", shipping_note="独立备注")
    response = client.post(url, data, HTTP_ACCEPT="application/json")
    assert response.status_code == 200 and "Location" not in response
    saved = response.json()
    assert saved["saved"] == {
        "condition": "",
        "cost": "60.00",
        "supplier": "新供应商",
        "supplier_wechat": "新微信",
        "shipping_note": "独立备注",
    }
    assert saved["revisions"] and saved["supplier_choices"] == [
        {"name": "新供应商", "wechat": "新微信"}
    ]
    retried = client.post(url, data, HTTP_ACCEPT="application/json")
    assert retried.json()["version"] == saved["version"]
    trade.refresh_from_db()
    assert trade.unit_cost_fen == 6000


def test_saved_suppliers_reused_in_product_and_order_forms(connection, admin_user, client):
    trade = make_trade(connection)
    client.force_login(admin_user)
    assert (
        client.post(
            reverse("wb-costs"),
            {
                "product": trade.product_id,
                "cost": "60",
                "supplier": "李龙",
                "supplier_wechat": "枫",
            },
        ).status_code
        == 302
    )
    for url in (reverse("wb-costs"), reverse("wb-detail", args=[trade.pk])):
        response = client.get(url)
        assert response.context["supplier_choices"] == [{"name": "李龙", "wechat": "枫"}]
        assert 'list="saved-suppliers"' in response.content.decode()


def test_supplier_choices_scope_deduplication_and_ambiguous_alias(connection):
    trade = make_trade(connection)
    ProductCost.objects.filter(pk=trade.product_id).update(supplier="共用", supplier_wechat="甲")
    Trade.objects.filter(pk=trade.pk).update(supplier="共用", supplier_wechat="甲")
    other = Shop.objects.create(name="其他店铺", is_active=False)
    ProductCost.objects.create(shop=other, key="other", supplier="不可见", supplier_wechat="私有")
    assert supplier_choices(connection.shop) == [{"name": "共用", "wechat": "甲"}]
    Trade.objects.filter(pk=trade.pk).update(supplier_wechat="乙")
    assert supplier_choices(connection.shop) == [{"name": "共用", "wechat": ""}]
