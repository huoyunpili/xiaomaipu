import hashlib
from unittest.mock import patch

import pytest
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from app.common.business import BusinessError
from app.workbench.models import SupplierAccess, SupplierVideo
from app.workbench.services import create_batches
from app.workbench.supplier_views import batch_text, legacy_video_path
from tests.test_workspace import connection, make_trade  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("chosen", [[], ["供应商甲"]])
def test_shipping_supplier_filter_is_optional(connection, admin_user, client, chosen):  # noqa: F811
    unassigned = make_trade(connection, number="100001")
    assigned = make_trade(connection, number="100002")
    assigned.supplier = "供应商甲"
    assigned.save(update_fields=["supplier"])
    client.force_login(admin_user)
    with patch("app.workbench.views.refresh_order"):
        response = client.post(
            reverse("wb-export", args=["shipping"]),
            {
                "selected": [str(unassigned.pk), str(assigned.pk)],
                "supplier_filter": "1",
                "export_supplier": chosen,
            },
        )
    assert response.status_code == 200
    batches = response.context["batches"]
    assert {b.supplier for b in batches} == ({"供应商甲"} if chosen else {"", "供应商甲"})
    if not chosen:
        batch = next(b for b in batches if not b.supplier)
        assert "未分配供应商" in response.content.decode()
        download = client.get(reverse("wb-batch-text", args=[batch.pk]))
        assert download.status_code == 200
        assert unassigned.number in download.content.decode()
        assert "未分配供应商" in download.content.decode()
        unassigned.refresh_from_db()
        assert unassigned.supplier == ""


@pytest.mark.parametrize("field,value", [("phone", ""), ("address", "***"), ("spec", "")])
def test_unassigned_shipping_still_requires_complete_details(connection, admin_user, field, value):  # noqa: F811
    trade = make_trade(connection)
    setattr(trade, field, value)
    trade.save(update_fields=[field])
    with pytest.raises(BusinessError, match="完整收件信息"):
        create_batches([trade], "shipping", admin_user)


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_browser_shipping_without_supplier(connection, admin_user, client, live_server, tmp_path):  # noqa: F811
    from playwright.sync_api import expect, sync_playwright

    trade = make_trade(connection)
    client.force_login(admin_user)
    with patch("app.workbench.views.refresh_order"), sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context()
        context.add_cookies(
            [
                {
                    "name": "sessionid",
                    "value": client.cookies["sessionid"].value,
                    "url": live_server.url,
                }
            ]
        )
        page = context.new_page()
        page.goto(live_server.url + reverse("wb-shipping"))
        expect(
            page.get_by_text("供货商筛选（可选；不勾选则导出所选订单，含未分配供应商）")
        ).to_be_visible()
        page.locator(f'input[name="selected"][value="{trade.pk}"]').check()
        page.get_by_role("button", name="导出发货单").click()
        expect(page.get_by_role("heading", name="清单已生成")).to_be_visible()
        page.get_by_role("link", name="未分配供应商 · 1 单 →").click()
        expect(page.locator("#shipping-text")).to_contain_text(trade.number)
        with page.expect_download() as downloaded:
            page.get_by_role("link", name="下载 TXT").click()
        path = tmp_path / "shipping.txt"
        downloaded.value.save_as(path)
        assert trade.number in path.read_text(encoding="utf-8")
        browser.close()


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
