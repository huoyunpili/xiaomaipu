from importlib import import_module
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.apps import apps
from django.urls import reverse

from app.integrations.client import APIError
from app.integrations.models import Connection, SyncRun
from app.integrations.services import connect, queue_sync
from app.integrations.tasks import poll_orders, sync_orders
from tests.test_integrations import connection  # noqa: F401

pytestmark = pytest.mark.django_db


def test_first_verification_starts_sync_without_switch(client, admin_user, shop):
    client.force_login(admin_user)
    with (
        patch(
            "app.integrations.client.XgjClient.call",
            return_value={"list": [{"is_valid": True, "authorize_id": 1234}]},
        ),
        patch("app.integrations.tasks.sync_orders.delay") as schedule,
    ):
        response = client.post(reverse("xgj-operate", args=["connect"]), follow=True)
    connected = Connection.objects.get()
    assert connected.enabled and connected.sync_start_at
    schedule.assert_called_once_with(str(SyncRun.objects.get().pk))
    assert "已自动启动订单同步" in response.content.decode()


def test_reverification_preserves_cursor_and_boundary(connection, admin_user):  # noqa: F811
    connection.cursor = 123456
    connection.save()
    api = MagicMock()
    api.call.return_value = {"list": [{"is_valid": True, "authorize_id": 1234}]}
    result = connect(actor=admin_user, client=api)
    assert result.enabled and result.cursor == 123456
    assert result.sync_start_at == connection.sync_start_at


def test_exhausted_network_retry_recovers_on_next_poll(connection):  # noqa: F811
    connection.enabled = True
    connection.save()
    run = queue_sync(connection)
    run.attempts = 3
    run.save()
    with patch(
        "app.integrations.client.XgjClient.call", side_effect=APIError("离线", retryable=True)
    ):
        sync_orders(str(run.pk))
    run.refresh_from_db()
    connection.refresh_from_db()
    assert run.status == "FAILED" and connection.enabled
    with (
        patch("app.integrations.client.XgjClient.call", return_value={"list": []}),
        patch("app.integrations.tasks.enrich_synced_orders.delay"),
    ):
        poll_orders()
    connection.refresh_from_db()
    assert connection.last_success and connection.error == ""
    assert connection.runs.filter(status="DONE").count() == 1


@pytest.mark.parametrize(
    "pending_credentials,missing_boundary", [(False, False), (True, False), (False, True)]
)
def test_upgrade_enables_only_ready_existing_connections(
    connection,  # noqa: F811
    pending_credentials,
    missing_boundary,
):
    connection.error = (
        "API 配置已更新，请验证连接后重新开启自动同步。" if pending_credentials else "离线"
    )
    if missing_boundary:
        connection.sync_start_at = None
    connection.save()
    migration = import_module("app.integrations.migrations.0007_default_automatic_sync")
    migration.enable_existing_connections(
        apps, SimpleNamespace(connection=SimpleNamespace(alias="default"))
    )
    connection.refresh_from_db()
    assert connection.enabled == (not pending_credentials and not missing_boundary)
