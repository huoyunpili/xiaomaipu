from io import BytesIO
from unittest.mock import Mock, patch

import pytest
from django.urls import reverse

from app.integrations.services import store_order
from app.shops.models import Shop
from app.workbench.models import Trade
from app.workbench.product_images import MAX_BYTES, cached_image
from tests.test_workspace import connection as workspace_connection
from tests.test_workspace import make_trade, payload

connection = workspace_connection
pytestmark = pytest.mark.django_db
IMAGE = b"\x89PNG\r\n\x1a\n" + b"test-image"


def test_expired_url_refreshes_image_without_changing_order(
    connection, admin_user, client, settings, tmp_path
):
    settings.PRIVATE_MEDIA_ROOT = tmp_path
    data = payload()
    data["goods"]["images"] = ["https://img.alicdn.com/expired.png"]
    row = store_order(connection, data)
    trade = Trade.objects.get(platform=row)
    fresh = {**data, "goods": {**data["goods"], "images": ["https://img.alicdn.com/fresh.png"]}}
    client.force_login(admin_user)
    with (
        patch("app.workbench.product_images.XgjClient.call", return_value=fresh) as api,
        patch("app.workbench.product_images.build_opener") as opener,
    ):
        opener.return_value.open.side_effect = [OSError("expired"), BytesIO(IMAGE)]
        response = client.get(reverse("wb-product-image", args=[trade.pk]))
        assert b"".join(response.streaming_content) == IMAGE
        api.assert_called_once()
    trade.refresh_from_db()
    assert trade.image.endswith("fresh.png") and trade.status == "SHIPPING"


def test_corrupt_cached_image_is_refetched(settings, tmp_path):
    settings.PRIVATE_MEDIA_ROOT = tmp_path
    with patch("app.workbench.product_images.build_opener") as opener:
        opener.return_value.open.side_effect = [BytesIO(IMAGE), BytesIO(IMAGE)]
        path, _ = cached_image("https://img.alicdn.com/corrupt.png")
        path.write_bytes(b"incomplete")
        repaired, _ = cached_image("https://img.alicdn.com/corrupt.png")
        assert repaired.read_bytes() == IMAGE
        assert opener.return_value.open.call_count == 2


def test_missing_thumbnail_restores_detail_without_changing_business_facts(
    connection, admin_user, client, settings, tmp_path
):
    settings.PRIVATE_MEDIA_ROOT = tmp_path
    trade = make_trade(connection, goods={**payload()["goods"], "images": []})
    original_status, original_paid = trade.status, trade.paid_fen
    data = payload()
    data["order_no"] = trade.number
    data["order_status"] = 22
    data["pay_amount"] = 1
    data["goods"]["images"] = ["https://img.alicdn.com/restored.png"]
    client.force_login(admin_user)
    with (
        patch("app.workbench.product_images.XgjClient.call", return_value=data) as detail,
        patch("app.workbench.product_images.build_opener") as opener,
    ):
        opener.return_value.open.return_value = BytesIO(IMAGE)
        response = client.get(reverse("wb-product-image", args=[trade.pk]))
        assert response.status_code == 200
        assert b"".join(response.streaming_content) == IMAGE
        detail.assert_called_once()
    trade.refresh_from_db()
    assert trade.image.endswith("restored.png")
    assert (trade.status, trade.paid_fen) == (original_status, original_paid)
    assert trade.platform.snapshot["pay_amount"] == original_paid
    Trade.objects.filter(pk=trade.pk).update(image="")
    with patch("app.workbench.product_images.XgjClient.call") as detail:
        response = client.get(reverse("wb-product-image", args=[trade.pk]))
        assert b"".join(response.streaming_content) == IMAGE
        detail.assert_not_called()


def test_thumbnail_rejects_wrong_order_and_can_retry(connection, admin_user, client):
    trade = make_trade(connection, goods={**payload()["goods"], "images": []})
    client.force_login(admin_user)
    with patch("app.workbench.product_images.XgjClient.call", return_value=payload(number="999")):
        response = client.get(reverse("wb-product-image", args=[trade.pk]))
    assert response.status_code == 503
    assert response["Cache-Control"] == "no-store"
    trade.refresh_from_db()
    assert trade.image == ""


def test_thumbnail_uses_second_image_when_first_is_unavailable(
    connection, admin_user, client, settings, tmp_path
):
    settings.PRIVATE_MEDIA_ROOT = tmp_path
    data = payload()
    data["goods"]["images"] = [
        "https://img.alicdn.com/broken.png",
        "https://img.alicdn.com/working.png",
    ]
    row = store_order(connection, data)
    trade = Trade.objects.get(platform=row)
    client.force_login(admin_user)
    with patch("app.workbench.product_images.build_opener") as opener:
        opener.return_value.open.side_effect = [OSError("offline"), BytesIO(IMAGE)]
        response = client.get(reverse("wb-product-image", args=[trade.pk]))
        assert b"".join(response.streaming_content) == IMAGE
    trade.refresh_from_db()
    assert trade.image.endswith("working.png")


def test_missing_images_have_loadable_urls_on_order_and_cost_pages(connection, admin_user, client):
    trade = make_trade(connection, goods={**payload()["goods"], "images": []})
    client.force_login(admin_user)
    url = reverse("wb-product-image", args=[trade.pk])
    for page in ("wb-orders", "wb-costs"):
        response = client.get(reverse(page))
        assert response.status_code == 200
        assert f'src="{url}"' in response.content.decode()


def test_images_survive_saved_history_projection_and_same_version_refresh(connection):
    from app.workbench.services import project

    original = payload()
    first = "https://img.alicdn.com/first.png"
    second = "https://img.alicdn.com/second.png"
    original["goods"]["images"] = [first]
    row = store_order(connection, original)
    Trade.objects.filter(platform=row).update(image="")
    assert project(row).image == first
    original["goods"]["images"] = [second]
    row = store_order(connection, original)
    assert not any("同一更新时间" in issue for issue in row.contract_issues)
    assert project(row).image == second
    del original["goods"]["images"]
    row = store_order(connection, original)
    assert project(row).image == second


def test_same_version_detail_enrichment_does_not_fake_conflict(connection):
    original = payload()
    row = store_order(connection, original)
    enriched = {
        **original,
        "create_time": original["order_time"],
        "cancel_time": 0,
        "consign_time": 0,
    }
    store_order(connection, enriched)
    row.refresh_from_db()
    trade = Trade.objects.get(platform=row)
    assert trade.status == "SHIPPING"
    assert not any("同一更新时间" in issue for issue in row.contract_issues)
    assert row.snapshot["create_time"] == original["order_time"]
    store_order(connection, {**enriched, "pay_amount": 1})
    trade.refresh_from_db()
    row.refresh_from_db()
    assert trade.status == "REVIEW"
    assert row.snapshot["pay_amount"] == original["pay_amount"]


def test_product_image_cache_survives_cdn_failure_and_enforces_access(
    connection, admin_user, client, settings, tmp_path
):
    settings.PRIVATE_MEDIA_ROOT = tmp_path
    trade = make_trade(connection)
    trade.image = "http://img.alicdn.com/test-product.png"
    trade.save()
    url = reverse("wb-product-image", args=[trade.pk])
    assert client.get(url).status_code == 302
    client.force_login(admin_user)
    opener = Mock()
    opener.open.return_value = BytesIO(IMAGE)
    with patch("app.workbench.product_images.build_opener", return_value=opener):
        response = client.get(url)
        assert response.status_code == 200
        assert b"".join(response.streaming_content) == IMAGE
        assert response["Content-Type"] == "image/png"
        assert response["Cache-Control"] in ("private, max-age=3600", "no-store")
        assert opener.open.call_args.args[0].full_url.startswith("https://img.alicdn.com/")
    with patch(
        "app.workbench.product_images.build_opener", side_effect=OSError("offline")
    ) as network:
        response = client.get(url)
        assert b"".join(response.streaming_content) == IMAGE
        network.assert_not_called()
    other = Shop.objects.create(name="其他店铺", is_active=False)
    trade.shop = other
    trade.save()
    assert client.get(url).status_code == 404


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/image",
        "https://127.0.0.1/image",
        "https://img.alicdn.com.evil.example/image",
        "https://user@img.alicdn.com/image",
    ],
)
def test_product_image_rejects_untrusted_origins(url, settings, tmp_path):
    settings.PRIVATE_MEDIA_ROOT = tmp_path
    with patch("app.workbench.product_images.build_opener") as network:
        with pytest.raises(ValueError):
            cached_image(url)
        network.assert_not_called()


@pytest.mark.parametrize(
    "data", [b"<svg>not allowed</svg>", IMAGE + b"x" * MAX_BYTES], ids=["svg", "oversized"]
)
def test_product_image_rejects_invalid_or_oversized_content(data, settings, tmp_path):
    settings.PRIVATE_MEDIA_ROOT = tmp_path
    opener = Mock()
    opener.open.return_value = BytesIO(data)
    with patch("app.workbench.product_images.build_opener", return_value=opener):
        with pytest.raises(ValueError):
            cached_image("https://img.alicdn.com/test-product.png")
    assert not list((tmp_path / "product-images").iterdir())
