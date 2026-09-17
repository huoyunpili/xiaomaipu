from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from app.common.business import BusinessError
from app.integrations.client import APIError, APIRejected
from app.workbench.models import SupplierAccess, SupplierDispatch, SupplierVideo, Trade
from app.workbench.services import create_batches
from app.workbench.supplier_service import (
    ensure_access,
    process_dispatch,
    reconcile_dispatch,
    save_video,
    submit_dispatch,
    video_path,
)
from app.workbench.supplier_views import batch_text
from tests.test_workspace import connection, make_trade  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def shipment(connection, admin_user, settings, tmp_path):  # noqa: F811
    settings.PRIVATE_MEDIA_ROOT = tmp_path
    cache.clear()
    cache.set("supplier_carriers", [{"code": "shunfeng", "name": "顺丰速运"}])
    trade = make_trade(connection)
    trade.supplier = "李龙"
    trade.save()
    batch = create_batches([trade], "shipping", admin_user)[0]
    return trade, ensure_access(batch)


def video():
    return SimpleUploadedFile(
        "发货前.mp4", b"\x00\x00\x00\x18ftypisom" + b"0" * 100, content_type="video/mp4"
    )


def test_revoked_during_upload_is_not_saved(shipment):
    trade, access = shipment
    upload = video()
    original_chunks = upload.chunks

    def chunks(*args, **kwargs):
        SupplierAccess.objects.filter(pk=access.pk).update(revoked_at=timezone.now())
        yield from original_chunks(*args, **kwargs)

    upload.chunks = chunks
    with pytest.raises(BusinessError, match="失效"):
        save_video(access, trade.pk, upload)
    assert not SupplierVideo.objects.exists()


def test_waybill_normalization_is_idempotent(shipment):
    trade, access = shipment
    first = submit_dispatch(access, trade.pk, "shunfeng", " sf1234567890123 ")
    second = submit_dispatch(access, trade.pk, "shunfeng", "SF1234567890123")
    assert first.pk == second.pk and first.waybill == "SF1234567890123"


def test_uncertain_dispatches_all_get_rechecked(shipment):
    from app.workbench.tasks import poll_supplier_dispatches

    trade, access = shipment
    ids = []
    for index in range(21):
        item = Trade.objects.create(shop=trade.shop, number=f"QUEUE-{index}")
        dispatch = SupplierDispatch.objects.create(
            trade=item,
            access=access,
            state="UNKNOWN",
            submitted_at=timezone.now() - timedelta(minutes=3),
        )
        ids.append(dispatch.pk)
    with (
        patch("app.workbench.supplier_service.refresh_order", side_effect=APIError("offline")),
        patch(
            "app.workbench.supplier_service.reconcile_dispatch", wraps=reconcile_dispatch
        ) as checked,
    ):
        poll_supplier_dispatches()
        poll_supplier_dispatches()
    assert {call.args[0] for call in checked.call_args_list} == set(ids)


def test_text_and_supplier_scope(shipment, client):
    trade, access = shipment
    text = batch_text(access.batch, "https://example.com/upload")
    assert trade.title in text and trade.spec in text and trade.address in text
    assert "https://example.com/upload" in text
    page = client.get(reverse("supplier-portal", args=[access.token]))
    assert page.status_code == 200
    assert trade.receiver.encode() in page.content
    assert trade.phone.encode() in page.content and trade.address.encode() in page.content
    assert trade.title.encode() in page.content
    assert page.headers["Referrer-Policy"] == "no-referrer"
    other = Trade.objects.create(
        shop=trade.shop,
        number="333111",
        title="另一个供货商",
        supplier="枫",
        receiver="其他清单收货人",
        phone="13999998888",
        address="其他清单专属地址",
    )
    page = client.get(reverse("supplier-portal", args=[access.token]))
    assert other.receiver.encode() not in page.content
    assert other.phone.encode() not in page.content and other.address.encode() not in page.content
    response = client.post(
        reverse("supplier-upload", args=[access.token, other.pk]), {"video": video()}
    )
    assert response.status_code == 400 and not SupplierVideo.objects.exists()
    response = client.post(
        reverse("supplier-shipping", args=[access.token, other.pk]),
        {"express_code": "shunfeng", "waybill": "SF12345678"},
    )
    assert response.status_code == 400 and not SupplierDispatch.objects.exists()


def test_video_durable_dedup_and_admin_only(shipment, client, admin_user):
    trade, access = shipment
    saved = save_video(access, trade.pk, video())
    assert video_path(saved.storage_name).read_bytes() == video().read()
    assert save_video(access, trade.pk, video()).pk == saved.pk
    assert trade.supplier_videos.count() == 1
    assert client.get(reverse("wb-supplier-video", args=[saved.pk])).status_code == 302
    client.force_login(admin_user)
    response = client.get(reverse("wb-supplier-video", args=[saved.pk]))
    assert b"ftyp" in b"".join(response.streaming_content)
    with pytest.raises(BusinessError):
        save_video(access, trade.pk, SimpleUploadedFile("fake.mp4", b"<html>bad</html>"))


@pytest.mark.parametrize("change", ["revoked", "expired", "reassigned", "other_shop"])
def test_invalid_access_cannot_upload_or_ship(shipment, client, change):
    trade, access = shipment
    if change == "revoked":
        access.revoked_at = timezone.now()
        access.save()
    elif change == "expired":
        access.expires_at = timezone.now() - timedelta(seconds=1)
        access.save()
    elif change == "reassigned":
        trade.supplier = "枫"
        trade.save()
    else:
        trade.shop.is_active = False
        trade.shop.save()
    assert client.post(
        reverse("supplier-upload", args=[access.token, trade.pk]), {"video": video()}
    ).status_code in (400, 404)
    assert client.post(
        reverse("supplier-shipping", args=[access.token, trade.pk]),
        {"express_code": "shunfeng", "waybill": "SF12345678"},
    ).status_code in (400, 404)
    assert not SupplierVideo.objects.exists() and not SupplierDispatch.objects.exists()


def test_csrf_and_isolated_gateway(shipment):
    trade, access = shipment
    client = Client(enforce_csrf_checks=True)
    assert (
        client.post(
            reverse("supplier-upload", args=[access.token, trade.pk]), {"video": video()}
        ).status_code
        == 403
    )
    with override_settings(ROOT_URLCONF="app.config.supplier_urls", DEBUG=False):
        for url in (
            "/",
            "/login/",
            "/workspace/orders/",
            "/integrations/xgj/",
            "/supplier/assets/../base.py",
        ):
            assert client.get(url).status_code == 404
        assert client.get(reverse("supplier-portal", args=[access.token])).status_code == 200


def test_submit_idempotent_and_changed_address_blocks(shipment):
    trade, access = shipment
    dispatch = submit_dispatch(access, trade.pk, "shunfeng", "SF12345678")
    assert submit_dispatch(access, trade.pk, "shunfeng", "SF12345678").pk == dispatch.pk
    with pytest.raises(BusinessError):
        submit_dispatch(access, trade.pk, "shunfeng", "SF99999999")
    dispatch.state = "FAILED"
    dispatch.save()
    trade.address = "新地址"
    trade.save()
    with pytest.raises(BusinessError):
        submit_dispatch(access, trade.pk, "shunfeng", "SF12345678")


def test_ship_once_then_readback(shipment):
    trade, access = shipment
    dispatch = submit_dispatch(access, trade.pk, "shunfeng", "SF12345678")
    row = trade.platform

    def refresh(*args):
        row.refresh_from_db()
        return row

    def shipped(*args, **kwargs):
        row.snapshot.update(
            order_status=21,
            consign_time=int(timezone.now().timestamp()),
            waybill_no="SF12345678",
            express_code="shunfeng",
        )
        row.save()
        return {}

    with (
        patch("app.workbench.supplier_service.refresh_order", side_effect=refresh),
        patch("app.workbench.supplier_service.XgjClient.call", side_effect=shipped) as call,
    ):
        process_dispatch(dispatch.pk)
        process_dispatch(dispatch.pk)
    assert call.call_count == 1
    assert call.call_args.args[0] == "ship"
    assert call.call_args.args[1]["order_no"] == trade.number
    dispatch.refresh_from_db()
    assert dispatch.state == "SUCCESS" and dispatch.confirmed_at


def test_timeout_never_resubmits(shipment):
    trade, access = shipment
    dispatch = submit_dispatch(access, trade.pk, "shunfeng", "SF12345678")
    with (
        patch("app.workbench.supplier_service.refresh_order", return_value=trade.platform),
        patch(
            "app.workbench.supplier_service.XgjClient.call",
            side_effect=APIError("timeout", retryable=True),
        ) as call,
    ):
        process_dispatch(dispatch.pk)
        process_dispatch(dispatch.pk)
        reconcile_dispatch(dispatch.pk)
    assert call.call_count == 1
    dispatch.refresh_from_db()
    assert dispatch.state == "UNKNOWN" and not dispatch.confirmed_at


def test_refund_before_dispatch_blocks_write(shipment):
    trade, access = shipment
    dispatch = submit_dispatch(access, trade.pk, "shunfeng", "SF12345678")
    trade.platform.snapshot["refund_status"] = 1
    trade.platform.save()
    with (
        patch("app.workbench.supplier_service.refresh_order"),
        patch("app.workbench.supplier_service.XgjClient.call") as call,
    ):
        process_dispatch(dispatch.pk)
    assert not call.called
    dispatch.refresh_from_db()
    assert dispatch.state == "FAILED"


def test_explicit_platform_rejection_allows_correction(shipment):
    trade, access = shipment
    dispatch = submit_dispatch(access, trade.pk, "shunfeng", "SF12345678")
    with (
        patch("app.workbench.supplier_service.refresh_order", return_value=trade.platform),
        patch(
            "app.workbench.supplier_service.XgjClient.call", side_effect=APIRejected("平台拒绝单号")
        ),
    ):
        process_dispatch(dispatch.pk)
    dispatch.refresh_from_db()
    assert dispatch.state == "FAILED" and "平台拒绝单号" in dispatch.message
    updated = submit_dispatch(access, trade.pk, "shunfeng", "SF99999999")
    assert updated.pk == dispatch.pk and updated.state == "READY"
