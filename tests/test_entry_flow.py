import uuid
from unittest.mock import patch

import pytest

from app.catalog.forms import NewProductForm
from app.catalog.models import SKU, Product
from app.catalog.services import create_product_with_stock
from app.common.models import IdempotencyRecord
from app.inventory.models import StockLot, StockMovement

pytestmark = pytest.mark.django_db


def test_create_with_stock_is_atomic_and_retry_safe(admin_user):
    data = dict(
        actor=admin_user,
        submission_key=uuid.uuid4(),
        name="屏幕",
        initial_quantity=3,
        unit_cost_fen=5000,
        condition_description="无配件，有漏光",
    )
    assert create_product_with_stock(**data) == create_product_with_stock(**data)
    sku = SKU.objects.get()
    assert sku.balance.available_qty == 3
    assert StockLot.objects.get().condition_description == "无配件，有漏光"
    assert StockMovement.objects.count() == 1


def test_stock_failure_leaves_no_half_created_product(admin_user):
    with patch("app.inventory.services.move_stock", side_effect=RuntimeError("failed")):
        with pytest.raises(RuntimeError):
            create_product_with_stock(
                actor=admin_user,
                submission_key=uuid.uuid4(),
                name="失败商品",
                initial_quantity=2,
                unit_cost_fen=5000,
            )
    assert not Product.objects.exists()
    assert not StockLot.objects.exists()
    assert not IdempotencyRecord.objects.exists()


def test_quantity_and_cost_validation():
    key = str(uuid.uuid4())
    assert NewProductForm({"name": "先存资料", "submission_key": key}).is_valid()
    form = NewProductForm({"name": "有货", "submission_key": key, "initial_quantity": 2})
    assert not form.is_valid() and "unit_cost" in form.errors
    assert NewProductForm(
        {"name": "赠品", "submission_key": key, "initial_quantity": 2, "unit_cost": "0"}
    ).is_valid()


def test_new_product_http_saves_condition_and_quantity_together(client, admin_user):
    client.force_login(admin_user)
    payload = {
        "name": "显示器",
        "condition_description": "99 新，翘边",
        "initial_quantity": 4,
        "unit_cost": "30.49",
        "submission_key": str(uuid.uuid4()),
    }
    assert client.post("/products/new/", payload).status_code == 302
    assert client.post("/products/new/", payload).status_code == 302
    assert SKU.objects.get().balance.available_qty == 4
    assert StockLot.objects.get().unit_cost_fen == 3049
    assert StockLot.objects.get().unit_freight_fen == 0
    assert client.get("/orders/new/").status_code == 200
    sku = SKU.objects.get()
    lot = StockLot.objects.get()
    response = client.get(f"/orders/new/{sku.pk}/?lot={lot.pk}")
    assert str(response.context["form"].initial["selected_lot"]) == str(lot.pk)
    assert client.get(f"/orders/new/{sku.pk}/?lot=bad-id").status_code == 404
