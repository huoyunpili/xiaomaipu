import io
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.db import IntegrityError, close_old_connections, transaction
from django.test import Client

from app.accounts.models import User
from app.audit.models import AuditEvent
from app.common.logging import SafeJsonFormatter
from app.common.models import IdempotencyRecord, OutboxEvent
from app.common.services import IdempotencyConflict
from app.shops.models import SalesChannel, Shop
from app.shops.services import StaleVersion, update_shop

pytestmark = pytest.mark.django_db


def test_bootstrap_preserves_existing_settings(shop):
    shop.name = "已经改好的店名"
    shop.save()
    call_command("bootstrap")
    shop.refresh_from_db()
    assert shop.name == "已经改好的店名"
    assert Shop.objects.count() == 1
    assert SalesChannel.objects.count() == 6
    with pytest.raises(IntegrityError), transaction.atomic():
        Shop.objects.create(name="第二家店")


def test_login_logout_and_permission_boundary(client, admin_user, operator, shop):
    assert client.get("/").status_code == 302
    assert client.post("/login/", {"username": "owner", "password": "wrong"}).status_code == 200
    assert (
        client.post("/login/", {"username": "owner", "password": "test-only-password"}).status_code
        == 302
    )
    response = client.get("/")
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    assert uuid.UUID(response["X-Request-ID"])
    assert client.get("/settings/shop/").status_code == 200
    assert client.get("/logout/").status_code == 405
    assert client.post("/logout/").status_code == 302
    client.force_login(operator)
    assert client.get("/").status_code == 200
    assert client.get("/settings/shop/").status_code == 403
    assert client.get("/audit/").status_code == 403
    assert client.post("/settings/shop/", {"name": "越权修改"}).status_code == 403
    with pytest.raises(PermissionDenied):
        update_shop(actor=operator, name="越权修改", version=1, submission_key=uuid.uuid4())
    shop.refresh_from_db()
    assert shop.version == 1


def test_csrf_is_required(admin_user, shop):
    client = Client(enforce_csrf_checks=True)
    client.force_login(admin_user)
    assert client.post("/settings/shop/", {"name": "不能提交"}).status_code == 403


def test_inactive_user_cannot_login(client, operator):
    operator.is_active = False
    operator.save()
    assert not client.login(username="staff", password="test-only-password")


def test_superuser_is_administrator():
    user = User.objects.create_superuser("root", password="test-only-password")
    assert user.is_shop_admin


def test_repeat_submission_conflict_and_stale_edit(admin_user, shop):
    key = uuid.uuid4()
    kwargs = dict(actor=admin_user, name="新店名", version=shop.version, submission_key=key)
    first = update_shop(**kwargs)
    assert update_shop(**kwargs) == first
    assert AuditEvent.objects.count() == OutboxEvent.objects.count() == 1
    with pytest.raises(IdempotencyConflict):
        update_shop(**{**kwargs, "name": "同一标识不同内容"})
    with pytest.raises(StaleVersion):
        update_shop(**{**kwargs, "submission_key": uuid.uuid4()})
    shop.refresh_from_db()
    assert shop.name == "新店名"
    assert shop.version == 2


def test_event_failure_rolls_back_business_audit_and_idempotency(admin_user, shop):
    with patch(
        "app.shops.services.OutboxEvent.objects.create", side_effect=RuntimeError("unavailable")
    ):
        with pytest.raises(RuntimeError):
            update_shop(
                actor=admin_user, name="不应保留", version=shop.version, submission_key=uuid.uuid4()
            )
    shop.refresh_from_db()
    assert shop.version == 1
    assert not AuditEvent.objects.exists()
    assert not IdempotencyRecord.objects.exists()
    assert not OutboxEvent.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_concurrent_duplicate_is_applied_once(admin_user, shop):
    key = uuid.uuid4()

    def submit():
        close_old_connections()
        try:
            return update_shop(actor=admin_user, name="并发保存", version=1, submission_key=key)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(), range(2)))
    assert results[0] == results[1]
    shop.refresh_from_db()
    assert shop.version == 2
    assert AuditEvent.objects.count() == OutboxEvent.objects.count() == 1


def test_settings_html_is_escaped_and_write_has_audit(client, admin_user, shop):
    client.force_login(admin_user)
    data = {"name": "<script>alert(1)</script>", "version": 1, "submission_key": str(uuid.uuid4())}
    assert client.post("/settings/shop/", data).status_code == 302
    assert client.post("/settings/shop/", data).status_code == 302
    response = client.get("/settings/shop/")
    assert b"<script>alert(1)</script>" not in response.content
    assert b"&lt;script&gt;" in response.content
    assert AuditEvent.objects.get().request_id


def test_health_hides_database_error(client):
    assert client.get("/health/").json() == {"status": "ok"}
    from django.db import OperationalError

    with patch(
        "app.common.views.connection.cursor", side_effect=OperationalError("private-password")
    ):
        response = client.get("/health/")
    assert response.status_code == 503
    assert b"private-password" not in response.content


def test_logs_exclude_arbitrary_credentials_and_query_strings():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(SafeJsonFormatter())
    record = logging.LogRecord(
        "test", logging.ERROR, "", 0, "AppSecret=not-for-logs phone=13800138000", (), None
    )
    handler.handle(record)
    assert "not-for-logs" not in stream.getvalue()
    assert "13800138000" not in stream.getvalue()
