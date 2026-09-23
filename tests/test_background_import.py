import os
import subprocess
import sys
import time
import uuid

import pytest
from celery import Celery
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile

from app.importing.models import ImportJob
from app.importing.services import HEADERS, preview_import
from app.orders.models import SalesOrder
from tests.test_business import goods


@pytest.mark.django_db(transaction=True)
@pytest.mark.filterwarnings("ignore:task_always_eager has no effect on send_task")
def test_real_worker_process_imports_isolated_test_database(admin_user, shop, tmp_path):
    sku, lot = goods(admin_user)
    payload = (",".join(HEADERS) + f"\nWORKER1,WECHAT,{sku.code},后台测试,1,90,\n").encode()
    job = preview_import(actor=admin_user, upload=SimpleUploadedFile("worker.csv", payload))
    ImportJob.objects.filter(pk=job.pk).update(status="QUEUED")
    queue = "verify_" + uuid.uuid4().hex
    broker_root = tmp_path / "queue"
    broker_options = {
        "data_folder_in": str(broker_root / "messages"),
        "data_folder_out": str(broker_root / "messages"),
        "data_folder_processed": str(broker_root / "processed"),
    }
    for folder in broker_options.values():
        os.makedirs(folder, exist_ok=True)
    env = dict(
        os.environ,
        POSTGRES_DB=settings.DATABASES["default"]["NAME"],
        DJANGO_SETTINGS_MODULE="app.config.settings.development",
        REDIS_URL="filesystem://",
        CELERY_FILESYSTEM_ROOT=str(broker_root),
    )
    assert env["POSTGRES_DB"].startswith("test_")
    sender = Celery(
        "worker-test-sender",
        broker="filesystem://",
        broker_transport_options=broker_options,
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "app.config.celery",
            "worker",
            "--pool=solo",
            "--concurrency=1",
            "-Q",
            queue,
            "--hostname=" + queue,
            "--without-gossip",
            "--without-mingle",
            "--loglevel=ERROR",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        sender.send_task("app.importing.tasks.run_import", args=[str(job.pk)], queue=queue)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            job.refresh_from_db()
            if job.status in ("DONE", "FAILED"):
                break
            time.sleep(0.2)
        assert job.status == "DONE", job.error
        assert SalesOrder.objects.get(external_order_no="WORKER1").amount_fen == 9000
    finally:
        sender.close()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
