import csv
import io
import struct
import zlib
from datetime import datetime, timedelta
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from app.common.business import BusinessError
from app.integrations.services import refresh_refund, store_refund
from app.workbench.exports import cleanup_images, private_path, save_image, validate_png
from app.workbench.models import ExportImage, Trade, WorkspaceSettings
from app.workbench.repayment import due_rows, filter_due
from app.workbench.services import classify, create_batches, update_product
from tests.test_workspace import connection as workspace_connection
from tests.test_workspace import make_trade, payload

connection = workspace_connection

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("day", ["2026-01-31", "2026-02-28", "2028-02-28", "2026-12-31"])
def test_next_three_days_across_month_leap_day_and_year(connection, admin_user, client, day):
    now = datetime.fromisoformat(day).replace(hour=23, minute=59, tzinfo=ZoneInfo("Asia/Shanghai"))
    midnight = now.replace(hour=0, minute=0)
    for index, offset in enumerate([0, 1, 2, 3, 4]):
        trade = make_trade(connection, number=str(77000 + index))
        trade.status = "PENDING"
        trade.shipped_at = midnight + timedelta(days=offset - 10)
        trade.save()
    with patch("app.workbench.views.timezone.now", return_value=now):
        client.force_login(admin_user)
        dashboard = client.get(reverse("dashboard"))
        detail = client.get(reverse("wb-pending"), {"due": "soon"})
    assert dashboard.context["soon_start"] == (now + timedelta(days=1)).date()
    assert dashboard.context["soon_end"] == (now + timedelta(days=3)).date()
    assert dashboard.context["soon_due"] == 60000
    assert {t.number for t in detail.context["rows"]} == {"77001", "77002", "77003"}


def test_repayment_boundaries_and_drilldown(connection, admin_user, client):
    now = datetime(2026, 9, 16, 12, tzinfo=ZoneInfo("Asia/Shanghai"))
    midnight = now.replace(hour=0)
    offsets = [-1, 0, 11, 12, 23, 24, 71, 72, 95, 96]
    for index, hour in enumerate(offsets):
        trade = make_trade(connection, number=str(123450000 + index))
        trade.status = "PENDING"
        trade.shipped_at = midnight + timedelta(hours=hour, days=-10)
        trade.save()
    for index, status in enumerate(["SHIPPING", "REFUNDING", "COMPLETED", "REFUNDED"]):
        trade = make_trade(connection, number=str(123460000 + index))
        trade.status = status
        trade.shipped_at = midnight - timedelta(days=10)
        trade.save()
    qs = Trade.objects.select_related("shop__workspacesettings")
    expected = {"today": {1, 2, 3, 4}, "soon": {5, 6, 7, 8}, "overdue": {0, 1, 2}}
    client.force_login(admin_user)
    for period, indices in expected.items():
        numbers = {str(123450000 + i) for i in indices}
        assert {t.number for t in due_rows(qs, period, now)} == numbers
        assert set(filter_due(qs, period, 10, now).values_list("number", flat=True)) == numbers
        with patch("app.workbench.views.timezone.now", return_value=now):
            response = client.get(reverse("wb-pending"), {"due": period})
            assert {t.number for t in response.context["rows"]} == numbers
            csv_response = client.get(reverse("wb-pending"), {"due": period, "export": "csv"})
            records = list(csv.reader(io.StringIO(csv_response.content.decode("utf-8-sig"))))
            assert {row[0] for row in records[1:]} == numbers
    with patch("app.workbench.views.timezone.now", return_value=now):
        dashboard = client.get("/")
    assert dashboard.context["today_due"] == 4 * 20000
    assert dashboard.context["soon_due"] == 4 * 20000
    assert dashboard.context["overdue_amount"] == 3 * 20000


def test_reference_setting_validation_and_preservation(connection, admin_user, client):
    trade = make_trade(connection)
    trade.status = "PENDING"
    trade.shipped_at = timezone.now()
    trade.save()
    config = WorkspaceSettings.objects.create(shop=connection.shop, image_retention_days=7)
    client.force_login(admin_user)
    for invalid in ["0", "366", "abc", ""]:
        response = client.post(
            reverse("wb-settings"), {"action": "reference", "reference_days": invalid}
        )
        assert response.status_code == 200
        config.refresh_from_db()
        assert config.reference_days == 10 and config.image_retention_days == 7
    response = client.post(reverse("wb-settings"), {"action": "reference", "reference_days": "13"})
    assert response.status_code == 302
    trade = Trade.objects.select_related("shop__workspacesettings").get(pk=trade.pk)
    assert trade.reference_at == trade.shipped_at + timedelta(days=13)
    config.refresh_from_db()
    assert config.image_retention_days == 7
    client.post(reverse("wb-settings"), {"image_retention_days": "14"})
    config.refresh_from_db()
    assert config.reference_days == 13
    trade.status = "REFUNDING"
    assert trade.reference_at is None
    trade.status = "COMPLETED"
    assert trade.reference_at is None


def test_product_notes_reused_without_overwriting_local_or_history(connection, admin_user):
    trade = make_trade(connection, seller_remark="平台要求")
    trade.note = "本单要求"
    trade.save()
    update_product(
        trade.product, 6000, "甲", admin_user, supplier_wechat="甲工厂", shipping_note="加固包装"
    )
    trade.refresh_from_db()
    assert trade.shipping_note == "加固包装\n平台要求\n本单要求"
    assert trade.supplier_wechat == "甲工厂"
    batch = create_batches([trade], "shipping", admin_user)[0]
    before_snapshot = batch.snapshot
    trade.product.refresh_from_db()
    version = trade.product.version
    update_product(
        trade.product,
        6000,
        "甲",
        admin_user,
        supplier_wechat="甲工厂新备注",
        shipping_note="防潮包装",
    )
    trade.refresh_from_db()
    trade.product.refresh_from_db()
    batch.refresh_from_db()
    assert trade.product.version == version
    assert trade.product.revisions.count() == 1
    assert batch.stale and batch.snapshot == before_snapshot
    assert trade.note == "本单要求" and trade.default_shipping_note == "防潮包装"
    changed = make_trade(
        connection, seller_remark="平台新要求", update_time=int(timezone.now().timestamp()) + 20
    )
    assert changed.note == "本单要求" and changed.platform_note == "平台新要求"
    assert changed.supplier_wechat == "甲工厂新备注"
    new = make_trade(connection, number="123459999")
    assert new.default_shipping_note == "防潮包装" and new.supplier_wechat == "甲工厂新备注"


def test_product_note_form_and_order_supplier_override(connection, admin_user, client):
    trade = make_trade(connection)
    client.force_login(admin_user)
    response = client.post(
        reverse("wb-costs"),
        {
            "product": str(trade.product_id),
            "cost": "60",
            "supplier": "甲",
            "supplier_wechat": "微信甲",
            "shipping_note": "不放价格单",
        },
    )
    assert response.status_code == 302
    trade.refresh_from_db()
    data = {
        field: getattr(trade, field)
        for field in (
            "supplier",
            "supplier_wechat",
            "spec",
            "receiver",
            "phone",
            "address",
            "note",
            "refund_note",
            "refund_waybill",
        )
    }
    data["note"] = "本单加固"
    assert client.post(reverse("wb-detail", args=[trade.pk]), data).status_code == 302
    trade.refresh_from_db()
    assert not trade.supplier_override
    data.update(supplier="乙", supplier_wechat="微信乙")
    client.post(reverse("wb-detail", args=[trade.pk]), data)
    trade.refresh_from_db()
    assert trade.supplier_override
    update_product(trade.product, 6000, "丙", admin_user, supplier_wechat="微信丙")
    trade.refresh_from_db()
    assert trade.supplier == "乙" and trade.supplier_wechat == "微信乙"
    assert trade.shipping_note == "不放价格单\n本单加固"
    response = client.get(reverse("wb-detail", args=[trade.pk]))
    assert "不放价格单" in response.content.decode() and "本单加固" in response.content.decode()


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({"order_status": 24, "pay_time": 0}, "CLOSED"),
        (
            {
                "order_status": 23,
                "refund_status": 5,
                "refund_time": 1700000000,
                "refund_amount": 20000,
            },
            "REFUNDED",
        ),
        ({"order_status": 23, "refund_status": 0, "refund_time": 1700000000}, "REVIEW"),
        ({"refund_status": 4}, "SHIPPING"),
        ({"refund_status": 6}, "REVIEW"),
        ({"refund_status": 8}, "REFUNDING"),
        ({"refund_status": 99, "refund_time": 1700000000, "refund_amount": 20000}, "REVIEW"),
        ({"refund_status": 5, "refund_amount": 20000}, "REVIEW"),
        ({"refund_status": 1, "apply_amount": 1000}, "REVIEW"),
        ({"consign_time": 1700000000}, "REVIEW"),
    ],
)
def test_documented_statuses_and_uncertain_fields(changes, expected):
    assert classify(payload(**changes))[0] == expected


def test_aftercare_detail_without_version_updates_refund_immediately(connection, admin_user):
    t = make_trade(connection)
    now = int(timezone.now().timestamp())
    data = {
        "order_no": t.number,
        "refund_no": "R1",
        "refund_status": 5,
        "refund_amount": 20000,
        "refund_time": now,
        "apply_time": now - 86400,
        "refund_type": 2,
        "timeout_type": "1 ",
        "waybill_no": "RETURN1",
    }
    refresh_refund(
        actor=admin_user, row_id=t.platform_id, client=Mock(call=Mock(return_value=data))
    )
    t.refresh_from_db()
    assert t.status == "REFUNDED" and t.guarantee_fen == 0 and t.refunded_fen == 20000
    assert t.refund_type == 2 and t.return_required == "需要退货" and t.refund_applied_at
    assert t.refund_waybill == "RETURN1"


def test_versioned_refund_projects_and_stale_response_cannot_undo(connection, admin_user):
    t = make_trade(connection)
    now = int(timezone.now().timestamp())
    data = {
        "order_no": t.number,
        "refund_no": "R1",
        "refund_status": 5,
        "refund_amount": 20000,
        "refund_time": now,
        "update_time": now + 1,
    }
    store_refund(connection, data)
    t.refresh_from_db()
    assert t.status == "REFUNDED"
    refresh_refund(
        actor=admin_user,
        row_id=t.platform_id,
        client=Mock(call=Mock(return_value={**data, "refund_status": 1, "update_time": now})),
    )
    t.refresh_from_db()
    assert t.status == "REFUNDED" and t.guarantee_fen == 0


def test_private_receiver_survives_post_shipping_empty_fields(connection):
    original = make_trade(connection)
    now = int(timezone.now().timestamp())
    current = make_trade(
        connection,
        order_status=21,
        consign_time=now,
        update_time=now + 1,
        receiver_name="",
        receiver_mobile="",
        address="",
    )
    assert (current.receiver, current.phone, current.address) == (
        original.receiver,
        original.phone,
        original.address,
    )


def png_bytes():
    def chunk(kind, value):
        return (
            struct.pack(">I", len(value))
            + kind
            + value
            + struct.pack(">I", zlib.crc32(kind + value))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 920, 1, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\0" + b"\xff" * (920 * 4)))
        + chunk(b"IEND", b"")
    )


def test_private_png_access_idempotency_and_cleanup(
    connection, admin_user, operator, client, tmp_path
):
    t = make_trade(connection)
    update_product(t.product, 6000, "甲", admin_user)
    batch = create_batches([t], "shipping", admin_user)[0]
    url = reverse("wb-batch-image", args=[batch.pk])
    with override_settings(PRIVATE_MEDIA_ROOT=tmp_path):
        assert client.get(reverse("wb-batch", args=[batch.pk])).status_code == 302
        client.force_login(operator)
        assert client.get(reverse("wb-batch", args=[batch.pk])).status_code == 403
        client.force_login(admin_user)
        strict_client = Client(enforce_csrf_checks=True)
        strict_client.force_login(admin_user)
        assert strict_client.post(url, {"page": 1}).status_code == 403
        response = client.post(url, {"page": 1, "image": SimpleUploadedFile("a.png", png_bytes())})
        assert response.status_code == 200
        download_url = response.json()["url"]
        downloaded = client.get(download_url)
        assert b"".join(downloaded.streaming_content) == png_bytes()
        assert (
            downloaded["Cache-Control"] == "no-store"
            and downloaded["X-Content-Type-Options"] == "nosniff"
        )
        saved = ExportImage.objects.get()
        assert save_image(batch, 1, png_bytes()).pk == saved.pk
        path = private_path(saved.storage_name)
        assert path.is_file()
        ExportImage.objects.filter(pk=saved.pk).update(
            created_at=timezone.now() - timedelta(days=10)
        )
        assert cleanup_images() == 0
        assert client.post(reverse("wb-settings"), {"image_retention_days": 0}).status_code == 200
        assert WorkspaceSettings.objects.get(shop=t.shop).image_retention_days is None
        assert client.post(reverse("wb-settings"), {"image_retention_days": 7}).status_code == 302
        assert cleanup_images(dry_run=True) == 1 and path.exists()
        assert cleanup_images() == 1 and not path.exists()
        batch.refresh_from_db()
        assert batch.snapshot and not ExportImage.objects.exists()
        assert client.get(download_url).status_code == 404
        assert save_image(batch, 1, png_bytes()).pk != saved.pk
        client.force_login(operator)
        assert client.post(url, {"page": 1}).status_code == 403


def test_png_validation_and_path_boundaries(tmp_path):
    validate_png(png_bytes())
    for data in (
        b"not an image",
        png_bytes()[:-3],
        png_bytes() + b"trailing",
        png_bytes().replace(b"IDAT", b"IDAX"),
    ):
        with pytest.raises(BusinessError):
            validate_png(data)
    with override_settings(PRIVATE_MEDIA_ROOT=tmp_path):
        for name in ("exports/../../secret.png", "other/file.png", "exports/../other/file.png"):
            with pytest.raises(BusinessError):
                private_path(name)
