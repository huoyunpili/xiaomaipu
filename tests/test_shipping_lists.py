import hashlib

import pytest
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from app.workbench.models import SupplierAccess, SupplierVideo
from app.workbench.services import create_batches
from app.workbench.supplier_views import batch_text, legacy_video_path
from tests.test_workspace import connection, make_trade  # noqa: F401

pytestmark = pytest.mark.django_db


def shipping_batch(connection, admin_user):  # noqa: F811
    trade = make_trade(connection)
    trade.supplier = "李龙"
    trade.save(update_fields=["supplier", "updated_at"])
    return trade, create_batches([trade], "shipping", admin_user)[0]


def test_shipping_export_is_text_only(connection, admin_user, client):  # noqa: F811
    trade, batch = shipping_batch(connection, admin_user)
    text = batch_text(batch)
    assert trade.title in text and trade.spec in text and trade.address in text
    assert "http://" not in text and "https://" not in text
    assert "上传" not in text and "提交发货" not in text

    client.force_login(admin_user)
    page = client.get(reverse("wb-batch", args=[batch.pk]))
    html = page.content.decode()
    assert page.status_code == 200
    assert "复制整份清单" in html and "下载 TXT" in html
    assert "生成供货商上传链接" not in html and "供货商入口" not in html
    assert not hasattr(batch, "access")


def test_public_supplier_routes_are_removed(client):
    for name in ("supplier-portal", "supplier-upload", "supplier-shipping", "supplier-status"):
        with pytest.raises(NoReverseMatch):
            reverse(name, args=["unused", "00000000-0000-0000-0000-000000000000"])
    assert client.get("/supplier/").status_code == 404


def test_local_order_file_import_route_is_removed(client):
    with pytest.raises(NoReverseMatch):
        reverse("wb-history")
    assert client.get("/workspace/history/").status_code == 404


def test_existing_supplier_video_remains_downloadable(
    connection,  # noqa: F811
    admin_user,
    client,
    settings,
    tmp_path,
):
    settings.PRIVATE_MEDIA_ROOT = tmp_path
    trade, batch = shipping_batch(connection, admin_user)
    access = SupplierAccess.objects.create(
        batch=batch,
        expires_at=timezone.now(),
        revoked_at=timezone.now(),
    )
    payload = b"\x00\x00\x00\x18ftypisom" + b"legacy" * 20
    storage_name = f"supplier-evidence/{trade.pk}/legacy.mp4"
    path = legacy_video_path(storage_name)
    path.parent.mkdir(parents=True)
    path.write_bytes(payload)
    video = SupplierVideo.objects.create(
        trade=trade,
        access=access,
        storage_name=storage_name,
        original_name="legacy.mp4",
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        mime_type="video/mp4",
    )

    assert client.get(reverse("wb-supplier-video", args=[video.pk])).status_code == 302
    client.force_login(admin_user)
    response = client.get(reverse("wb-supplier-video", args=[video.pk]))
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == payload
