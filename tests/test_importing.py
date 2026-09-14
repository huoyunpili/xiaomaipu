import io

import openpyxl
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from app.importing.models import ImportJob
from app.importing.services import HEADERS, execute_import, preview_import
from app.orders.models import SalesOrder
from tests.test_business import goods

pytestmark = pytest.mark.django_db


def test_custom_column_mapping(admin_user, shop):
    sku, lot = goods(admin_user)
    raw = f"单号,来源,货号,买家,件数,单价\nMAP1,WECHAT,{sku.code},客人,1,80\n".encode()
    mapping = dict(zip(HEADERS[:-1], ["单号", "来源", "货号", "买家", "件数", "单价"], strict=True))
    job = preview_import(
        actor=admin_user, upload=SimpleUploadedFile("mapped.csv", raw), column_mapping=mapping
    )
    assert job.rows.get().payload["external_order_no"] == "MAP1"


def test_csv_preview_errors_replay_and_existing_order_preserved(admin_user, shop):
    sku, lot = goods(admin_user)
    raw = (
        ",".join(HEADERS)
        + f"\nCSV1,WECHAT,{sku.code},买家,1,99.99,\nBAD,WECHAT,{sku.code},坏行,-1,5,\n"
    ).encode()

    def upload():
        return preview_import(actor=admin_user, upload=SimpleUploadedFile("orders.csv", raw))

    job = upload()
    assert upload().pk == job.pk
    assert job.rows.filter(status="ERROR").count() == 1
    assert not SalesOrder.objects.exists()
    ImportJob.objects.filter(pk=job.pk).update(status="QUEUED")
    execute_import(job.pk)
    execute_import(job.pk)
    order = SalesOrder.objects.get()
    assert order.status == "DRAFT" and order.amount_fen == 9999
    other = preview_import(
        actor=admin_user, upload=SimpleUploadedFile("changed.csv", raw.replace(b"99.99", b"88.88"))
    )
    ImportJob.objects.filter(pk=other.pk).update(status="QUEUED")
    execute_import(other.pk)
    assert other.rows.filter(status="SKIPPED").count() == 1
    order.refresh_from_db()
    assert order.amount_fen == 9999


def test_xlsx_and_invalid_uuid_are_row_errors(admin_user, shop):
    sku, lot = goods(admin_user)
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(HEADERS)
    sheet.append(["X1", "WECHAT", sku.code, "客人", 2, 1.25, "invalid"])
    sheet.append(["X2", "WECHAT", sku.code, "客人", 1, 2.50, str(lot.pk)])
    output = io.BytesIO()
    workbook.save(output)
    job = preview_import(
        actor=admin_user, upload=SimpleUploadedFile("sheet.xlsx", output.getvalue())
    )
    assert job.rows.filter(status="ERROR").count() == 1
    assert job.rows.filter(status="READY").count() == 1
