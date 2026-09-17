from importlib import import_module
from unittest.mock import patch

import pytest
from django.apps import apps
from django.db import connection as database
from django.test import override_settings
from django.urls import reverse

from app.integrations.models import Connection
from app.shops.models import Shop
from app.workbench.exports import save_image
from app.workbench.models import CostVersion, ProductCost, Trade
from app.workbench.services import create_batches, update_product
from tests.test_workspace import connection as workspace_connection
from tests.test_workspace import make_trade
from tests.test_workspace_hardening import png_bytes

connection = workspace_connection
pytestmark = pytest.mark.django_db


def test_shop_product_and_private_endpoints_are_isolated(connection, admin_user, client, tmp_path):
    local = make_trade(connection)
    other_shop = Shop.objects.create(name="历史隔离店铺", is_active=False)
    other_connection = Connection.objects.create(
        shop=other_shop,
        actor=admin_user,
        seller_id="other",
        sync_start_at=connection.sync_start_at,
    )
    other = make_trade(other_connection)
    assert local.product_id != other.product_id
    update_product(
        other.product,
        8800,
        "隔离供应商",
        admin_user,
        supplier_wechat="隔离微信",
        shipping_note="隔离发货备注",
    )
    local.refresh_from_db()
    assert local.unit_cost_fen is None and not local.supplier
    other.refresh_from_db()
    batch = create_batches([other], "shipping", admin_user)[0]
    client.force_login(admin_user)
    for name in ["wb-orders", "wb-shipping", "wb-costs", "dashboard", "wb-profits"]:
        response = client.get(reverse(name))
        assert response.status_code == 200
        assert "隔离供应商" not in response.content.decode()
        assert "隔离微信" not in response.content.decode()
    assert client.get(reverse("wb-detail", args=[other.pk])).status_code == 404
    assert client.post(reverse("wb-detail", args=[other.pk]), {}).status_code == 404
    assert client.get(reverse("wb-batch", args=[batch.pk])).status_code == 404
    assert client.post(reverse("wb-batch-image", args=[batch.pk]), {"page": 1}).status_code == 404
    assert (
        client.post(
            reverse("wb-costs"),
            {
                "product": str(other.product_id),
                "cost": "1",
                "supplier": "越界修改",
            },
        ).status_code
        == 404
    )
    other.product.refresh_from_db()
    assert other.product.unit_fen == 8800
    with patch("app.workbench.views.refresh_order") as refresh:
        client.post(reverse("wb-export", args=["shipping"]), {"selected": [str(other.pk)]})
        refresh.assert_not_called()
    with override_settings(PRIVATE_MEDIA_ROOT=tmp_path):
        image = save_image(batch, 1, png_bytes())
        assert client.get(reverse("wb-image", args=[image.pk])).status_code == 404


def test_legacy_shared_product_migration_preserves_snapshots(connection, admin_user):
    first = make_trade(connection)
    update_product(first.product, 6000, "原供应商", admin_user)
    first.refresh_from_db()
    product = first.product
    # Reproduce the intermediate migration state: nullable owner plus legacy shared references.
    ProductCost.objects.filter(pk=product.pk).update(shop=None)
    other_shop = Shop.objects.create(name="历史店铺", is_active=False)
    second = Trade.objects.create(
        shop=other_shop,
        product=product,
        number="legacy-other",
        unit_cost_fen=5000,
        cost_version=1,
        paid_fen=9000,
    )
    orphan = ProductCost.objects.create(key="orphan", title="不可推断归属的旧配置")
    before_revisions = list(
        CostVersion.objects.filter(product=product).values_list(
            "version", "unit_fen", "effective_at"
        )
    )
    migration = import_module("app.workbench.migrations.0007_product_shop")
    migration.split_products(apps, database.schema_editor())
    first.refresh_from_db()
    second.refresh_from_db()
    orphan.refresh_from_db()
    assert first.product_id != second.product_id
    assert first.product.shop_id == first.shop_id and second.product.shop_id == second.shop_id
    assert first.unit_cost_fen == 6000 and second.unit_cost_fen == 5000
    assert first.cost_version == 2 and second.cost_version == 1
    for item in (first.product, second.product):
        assert item.supplier == "原供应商"
        assert (
            list(item.revisions.values_list("version", "unit_fen", "effective_at"))
            == before_revisions
        )
    assert orphan.shop_id is None
