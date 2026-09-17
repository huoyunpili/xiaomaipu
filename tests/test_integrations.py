import json
import os
import subprocess
import sys
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from django.core.exceptions import PermissionDenied
from django.db import close_old_connections
from django.test import override_settings

from app.common.business import BusinessError
from app.integrations.client import APIError, XgjClient, signature
from app.integrations.models import Connection, PlatformOrder, PushNotice, SyncRun
from app.integrations.services import (
    connect,
    convert_order,
    execute_sync,
    queue_sync,
    refresh_refund,
    store_order,
)
from app.integrations.tasks import enqueue, process_notices, sync_orders
from app.inventory.models import StockMovement
from app.orders.models import SalesOrder
from tests.test_business import draft, goods

pytestmark = pytest.mark.django_db


def data(number="123456789", updated=100, **changes):
    return {
        "order_no": number,
        "update_time": updated,
        "order_status": 12,
        "total_amount": 12000,
        "pay_amount": 12000,
        "refund_amount": 0,
        "buyer_nick": "测试买家",
        "goods": {"quantity": 1, "price": 12000, "title": "测试商品"},
        **changes,
    }


@pytest.fixture
def connection(admin_user, shop):
    return Connection.objects.create(
        shop=shop,
        actor=admin_user,
        seller_id="1234",
        first_connected_at=datetime(1970, 1, 1, tzinfo=UTC),
        sync_start_at=datetime(1970, 1, 1, tzinfo=UTC),
        sync_start_basis="TEST",
    )


def test_signature_matches_official_example():
    assert (
        signature(
            "203413189371893",
            "o9wl81dncmvby3ijpq7eur456zhgtaxs",
            1636087298,
            b'{"product_id":"219530767978565"}',
        )
        == "c26c8a48809141f3dd80bd9b9ddb41ea"
    )


def test_connect_requires_admin_and_unique_authorization(operator, admin_user, shop):
    client = MagicMock()
    client.call.return_value = {"list": [{"is_valid": True, "authorize_id": 1234}]}
    with pytest.raises(PermissionDenied):
        connect(actor=operator, client=client)
    assert not client.call.called
    con = connect(actor=admin_user, client=client)
    assert con.seller_id == "1234" and not con.enabled
    client.call.return_value = {"list": [{"is_valid": False, "authorize_id": 1234}]}
    with pytest.raises(APIError):
        connect(actor=admin_user, client=client)


def test_duplicates_stale_and_null_updates_preserve_business(connection, admin_user, shop):
    original = data(receiver_mobile="secret-mobile", pay_no="secret-payment")
    row = store_order(connection, original)
    assert "secret" not in json.dumps(row.snapshot)
    store_order(connection, original)
    store_order(connection, data(updated=100, pay_amount=999))
    row.refresh_from_db()
    assert row.snapshot["pay_amount"] == 12000
    assert any("同一更新时间" in issue for issue in row.contract_issues)
    store_order(connection, data(updated=99, pay_amount=1))
    row.refresh_from_db()
    assert row.snapshot["pay_amount"] == 12000
    store_order(connection, data(updated=101, pay_amount=None, buyer_nick="", goods=None))
    row.refresh_from_db()
    assert row.snapshot["pay_amount"] == 12000 and row.snapshot["goods"]["quantity"] == 1
    assert PlatformOrder.objects.count() == 1 and SalesOrder.objects.count() == 0


def test_conversion_replay_and_manual_order_preserved(connection, admin_user):
    sku, lot = goods(admin_user)
    count = StockMovement.objects.count()
    row = store_order(connection, data())
    args = dict(actor=admin_user, row_id=row.pk, sku_id=sku.pk, quantity=1, unit_price_fen=12000)
    order = convert_order(**args)
    assert convert_order(**args).pk == order.pk
    assert order.status == "DRAFT" and order.received_fen == 0 and order.cost_fen == 0
    store_order(connection, data(updated=102, pay_amount=0, order_status=23))
    order.refresh_from_db()
    assert order.amount_fen == 12000 and order.status == "DRAFT"
    assert StockMovement.objects.count() == count
    other = draft(admin_user, sku, channel="XIANYU", price=500, external_order_no="987654321")
    row = store_order(connection, data(number="987654321"))
    args["row_id"] = row.pk
    assert convert_order(**args).pk == other.pk
    other.refresh_from_db()
    assert other.amount_fen == 500


def test_condition_validation_rolls_back_link(connection, admin_user):
    sku, lot = goods(admin_user)
    sku.requires_explicit_lot_selection = True
    sku.save()
    row = store_order(connection, data())
    with pytest.raises(BusinessError):
        convert_order(actor=admin_user, row_id=row.pk, sku_id=sku.pk, quantity=1, unit_price_fen=1)
    assert not SalesOrder.objects.exists()
    row.refresh_from_db()
    assert row.order_id is None


def test_sync_pagination_and_resumption(connection):
    client = MagicMock()
    client.call.side_effect = [
        {"list": [data(number=str(1000 + i)) for i in range(100)]},
        APIError("offline", retryable=True),
    ]
    run = queue_sync(connection)
    assert queue_sync(connection).pk == run.pk
    with pytest.raises(APIError):
        execute_sync(run.pk, client)
    run.refresh_from_db()
    assert run.page == 2 and run.status == "RETRY" and run.count == 100
    assert connection.last_success is None
    client.call.side_effect = None
    client.call.return_value = {"list": [data(number="5000")]}
    execute_sync(run.pk, client)
    run.refresh_from_db()
    connection.refresh_from_db()
    assert run.status == "DONE" and run.count == 101 and connection.last_success
    assert PlatformOrder.objects.count() == 101


def test_invalid_page_does_not_advance_checkpoint(connection):
    client = MagicMock()
    client.call.return_value = {"list": [data(), {"order_no": "bad"}]}
    run = queue_sync(connection)
    with pytest.raises(APIError):
        execute_sync(run.pk, client)
    run.refresh_from_db()
    assert run.page == 1 and run.status == "FAILED"
    assert not PlatformOrder.objects.exists()


@override_settings(XGJ_APP_KEY="test-key", XGJ_APP_SECRET="test-secret")
def test_signed_push_durable_replay_and_invalid_signature(client, connection):
    body = json.dumps({"order_no": "123456789", "modify_time": 100}).encode()
    stamp = int(time.time())
    sign = signature("test-key", "test-secret", stamp, body)
    url = f"/integrations/xgj/push/?appid=test-key&timestamp={stamp}&sign={sign}"
    assert (
        client.post(url, data=body, content_type="application/json").json()["result"] == "success"
    )
    assert client.post(url, data=body, content_type="application/json").status_code == 200
    assert PushNotice.objects.count() == 1 and not PlatformOrder.objects.exists()
    assert client.post(url, data=b"{}", content_type="application/json").status_code == 403
    with patch("app.integrations.tasks.XgjClient.call", return_value=data()):
        process_notices()
    assert PushNotice.objects.get().status == "DONE" and PlatformOrder.objects.count() == 1


def test_notice_mismatched_detail_never_imported(connection):
    PushNotice.objects.create(connection=connection, fingerprint="test", external_order_no="555")
    with patch("app.integrations.tasks.XgjClient.call", return_value=data()):
        process_notices()
    assert not PlatformOrder.objects.exists()
    assert PushNotice.objects.get().status == "FAILED"


def test_queue_outage_and_authorization_failure(connection):
    run = queue_sync(connection)
    with patch("app.integrations.tasks.sync_orders.delay", side_effect=OSError):
        enqueue(run)
    run.refresh_from_db()
    assert run.status == "FAILED"
    connection.enabled = True
    connection.save()
    run = queue_sync(connection)
    with patch("app.integrations.client.XgjClient.call", side_effect=APIError("授权失效")):
        sync_orders(str(run.pk))
    connection.refresh_from_db()
    assert not connection.enabled and connection.error == "授权失效"


@override_settings(XGJ_APP_KEY="test-key", XGJ_APP_SECRET="test-secret")
def test_client_suppresses_signed_url_and_buyer_payload():
    opener = MagicMock()
    opener.open.side_effect = urllib.error.URLError("secret-url-and-buyer")
    with patch("urllib.request.build_opener", return_value=opener), pytest.raises(APIError) as exc:
        XgjClient().call("orders")
    assert exc.value.retryable and "secret" not in str(exc.value)
    opener.open.side_effect = None
    opener.open.return_value.__enter__.return_value.read.return_value = (
        b'{"code":123,"msg":"secret"}'
    )
    with patch("urllib.request.build_opener", return_value=opener), pytest.raises(APIError) as exc:
        XgjClient().call("orders")
    assert not exc.value.retryable and "secret" not in str(exc.value)


@override_settings(XGJ_APP_KEY="test-key", XGJ_APP_SECRET="test-secret")
def test_client_explains_permission_error_and_stops_retrying():
    opener = MagicMock()
    opener.open.return_value.__enter__.return_value.read.return_value = b'{"code":100008}'
    with patch("urllib.request.build_opener", return_value=opener), pytest.raises(APIError) as exc:
        XgjClient().call("orders")
    assert not exc.value.retryable
    assert "100008" in str(exc.value) and "停止自动重试" in str(exc.value)


def test_platform_pages_show_actionable_orders_and_collapse_recovered_errors(
    client, connection, operator
):
    now = int(time.time())
    row = store_order(connection, data(order_time=now, update_time=now, pay_amount=12345))
    failure = SyncRun.objects.create(connection=connection, status="FAILED", error="旧的权限错误")
    connection.last_success = failure.created_at
    connection.save()
    client.force_login(operator)

    workspace = client.get("/workspace/orders/").content.decode()
    assert row.external_order_no in workspace and "测试商品" in workspace and "123.45" in workspace

    page = client.get("/integrations/xgj/").content.decode()
    assert "现在有什么数据" in page and "最近 20 次运行记录" in page
    assert "旧的权限错误" not in page and "后续同步已经成功恢复" in page
    assert row.external_order_no in page and "123.45" in page

    history = client.get("/integrations/xgj/?history=1").content.decode()
    assert "旧的权限错误" in history


def test_pages_permissions_and_conversion(client, connection, operator, admin_user):
    row = store_order(connection, data())
    assert client.get("/integrations/xgj/").status_code == 302
    client.force_login(operator)
    assert client.post("/integrations/xgj/sync/").status_code == 403
    assert client.get("/integrations/xgj/").status_code == 200
    assert client.get(f"/integrations/xgj/order/{row.pk}/").status_code == 200
    client.force_login(admin_user)
    sku, lot = goods(admin_user)
    response = client.post(
        f"/integrations/xgj/order/{row.pk}/",
        {
            "sku": str(sku.pk),
            "quantity": 1,
            "price": "120",
            "confirmed": "on",
            "action": "convert",
        },
    )
    assert response.status_code == 302
    assert SalesOrder.objects.get().amount_fen == 12000


def test_incremental_window_and_failed_cursor_preserved(connection):
    now = int(time.time())
    connection.cursor = now - 1000
    connection.save()
    run = queue_sync(connection)
    client = MagicMock()
    client.call.return_value = {"list": [data(updated=now - 500)]}
    execute_sync(run.pk, client)
    assert client.call.call_args.args[1]["update_time"] == [now - 1600, run.window_end]
    connection.refresh_from_db()
    assert connection.cursor == run.window_end
    run = queue_sync(connection)
    client.call.return_value = {"list": [data(updated=1)]}
    with pytest.raises(APIError):
        execute_sync(run.pk, client)
    connection.refresh_from_db()
    assert connection.cursor == now


@pytest.mark.django_db(transaction=True)
def test_concurrent_conversion_creates_one_order(connection, admin_user):
    sku, lot = goods(admin_user)
    row = store_order(connection, data())

    def convert(_):
        close_old_connections()
        try:
            return convert_order(
                actor=admin_user, row_id=row.pk, sku_id=sku.pk, quantity=1, unit_price_fen=12000
            ).pk
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(convert, range(2)))
    assert ids[0] == ids[1] and SalesOrder.objects.count() == 1


def test_retry_budget_and_page_limit_visible(connection):
    run = queue_sync(connection)
    with (
        patch(
            "app.integrations.client.XgjClient.call",
            side_effect=APIError("timeout", retryable=True),
        ),
        patch("app.integrations.tasks.sync_orders.apply_async") as retry,
    ):
        for _ in range(3):
            sync_orders(str(run.pk))
    run.refresh_from_db()
    assert run.status == "FAILED" and run.attempts == 3 and retry.call_count == 2
    run = queue_sync(connection)
    run.page = 100
    run.save()
    client = MagicMock()
    client.call.return_value = {"list": [data(number=str(1000 + i)) for i in range(100)]}
    with pytest.raises(APIError, match="10000"):
        execute_sync(run.pk, client)
    run.refresh_from_db()
    assert run.status == "FAILED" and connection.last_success is None


def test_fresh_web_process_loads_project_queue():
    script = (
        "import django; django.setup(); "
        "from app.integrations.tasks import sync_orders; "
        "from app.config.celery import app; "
        "from django.conf import settings; "
        "assert sync_orders.app is app; "
        "assert app.conf.broker_url == settings.CELERY_BROKER_URL"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "app.config.settings.test"},
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr


def test_refund_detail_is_reconciliation_only(connection, admin_user):
    row = store_order(connection, data())
    client = MagicMock()
    client.call.return_value = {
        "order_no": row.external_order_no,
        "apply_amount": 1000,
        "refund_amount": 0,
        "refund_status": 1,
        "receiver_mobile": "private",
    }
    refresh_refund(actor=admin_user, row_id=row.pk, client=client)
    row.refresh_from_db()
    assert row.refund_snapshot["apply_amount"] == 1000 and row.refund_checked_at
    assert "private" not in json.dumps(row.refund_snapshot)
    assert not SalesOrder.objects.exists()
    client.call.return_value = {"order_no": "999"}
    with pytest.raises(APIError):
        refresh_refund(actor=admin_user, row_id=row.pk, client=client)
    row.refresh_from_db()
    assert row.refund_snapshot["apply_amount"] == 1000
