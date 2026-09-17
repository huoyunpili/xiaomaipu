from datetime import UTC, date, datetime, timedelta
from importlib import import_module
from unittest.mock import MagicMock, patch

import pytest
from django.apps import apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import F
from django.utils import timezone

from app.common.business import BusinessError
from app.finance.models import MoneyEntry
from app.importing.services import execute_import, preview_import
from app.integrations.client import APIError
from app.integrations.models import (
    Connection,
    ExternalFactApplication,
    PlatformOrder,
    PlatformRefund,
    SyncRun,
)
from app.integrations.services import (
    BEIJING,
    confirm_sync_start,
    connect,
    execute_sync,
    queue_sync,
    store_order,
    store_refund,
)
from app.integrations.tasks import poll_orders
from app.inventory.models import StockMovement
from app.orders.models import SalesOrder, Shipment
from tests.test_business import draft, goods

pytestmark = pytest.mark.django_db


def platform_data(number, created, updated, **changes):
    return {
        "order_no": number,
        "order_time": created,
        "update_time": updated,
        "order_status": 12,
        "total_amount": 12000,
        "pay_amount": 12000,
        "refund_amount": 0,
        "buyer_nick": "测试买家",
        "goods": {"quantity": 1, "price": 12000, "title": "测试商品"},
        **changes,
    }


def ready_connection(admin_user, shop, start):
    return Connection.objects.create(
        shop=shop,
        actor=admin_user,
        seller_id="1234",
        first_connected_at=start,
        sync_start_at=start,
        sync_start_basis="TEST",
    )


def test_first_connection_fixes_beijing_day_and_reconnect_preserves(admin_user, shop):
    client = MagicMock()
    client.call.return_value = {"list": [{"is_valid": True, "authorize_id": 1234}]}
    first = datetime(2026, 9, 14, 3, 30, tzinfo=UTC)
    with patch("app.integrations.services.timezone.now", return_value=first):
        connection = connect(actor=admin_user, client=client)
    assert connection.sync_start_at == datetime(2026, 9, 13, 16, 0, tzinfo=UTC)
    assert connection.first_connected_at == first

    with patch(
        "app.integrations.services.timezone.now",
        return_value=first + timedelta(days=3),
    ):
        reconnected = connect(actor=admin_user, client=client)
    assert reconnected.sync_start_at == connection.sync_start_at
    assert reconnected.first_connected_at == first


def test_legacy_start_requires_one_fixed_admin_confirmation(admin_user, shop):
    connection = Connection.objects.create(shop=shop, actor=admin_user, seller_id="1234")
    with pytest.raises(APIError, match="起始日"):
        queue_sync(connection)
    confirm_sync_start(
        actor=admin_user,
        connection=connection,
        start_date=timezone.now().astimezone(BEIJING).date(),
    )
    connection.refresh_from_db()
    original = connection.sync_start_at
    assert queue_sync(connection).window_start == int(
        original.astimezone(BEIJING).replace(day=1).timestamp()
    )
    with pytest.raises(BusinessError, match="已经固定"):
        confirm_sync_start(
            actor=admin_user,
            connection=connection,
            start_date=date(2020, 1, 1),
        )


def test_creation_scope_incremental_updates_and_rescan_boundary(admin_user, shop):
    start = datetime.combine(
        timezone.now().astimezone(BEIJING).date(),
        datetime.min.time(),
        tzinfo=BEIJING,
    ).astimezone(UTC)
    connection = ready_connection(admin_user, shop, start)
    start_ts = int(start.timestamp())
    run = queue_sync(connection)
    client = MagicMock()
    client.call.return_value = {
        "list": [
            platform_data("1001", start_ts, start_ts + 10),
            platform_data("1002", start_ts - 1, start_ts + 20),
            platform_data("1003", 0, start_ts + 30),
        ]
    }
    execute_sync(run.pk, client)
    run.refresh_from_db()
    assert (run.scanned_count, run.accepted_count, run.historical_count, run.review_count) == (
        3,
        1,
        1,
        1,
    )
    assert PlatformOrder.objects.get(external_order_no="1001").scope_status == "IN_SCOPE"
    assert PlatformOrder.objects.get(external_order_no="1002").scope_status == "HISTORICAL"
    assert PlatformOrder.objects.get(external_order_no="1003").scope_status == "UNKNOWN"

    store_order(
        connection,
        platform_data("1001", start_ts, start_ts + 86400, order_status=22),
    )
    assert PlatformOrder.objects.get(external_order_no="1001").source_updated == start_ts + 86400
    assert queue_sync(connection, full=True).window_start == int(
        start.astimezone(BEIJING).replace(day=1).timestamp()
    )


def test_manual_order_and_import_share_platform_identity(admin_user, shop):
    start = datetime(2020, 1, 1, tzinfo=UTC)
    connection = ready_connection(admin_user, shop, start)
    sku, lot = goods(admin_user)
    manual = draft(
        admin_user,
        sku,
        channel="XIANYU",
        price=12000,
        external_order_no="2001",
    )
    movement_count = StockMovement.objects.count()
    row = store_order(connection, platform_data("2001", 1600000000, 1600000010))
    assert row.order_id == manual.pk and row.auto_matched
    assert StockMovement.objects.count() == movement_count

    second = store_order(connection, platform_data("2002", 1600000000, 1600000010))
    content = f"""渠道订单号,渠道代码,商品编码,客户称呼,数量,成交单价,实物组ID
2002,XIANYU,{sku.code},导入买家,1,120.00,
""".encode()
    job = preview_import(
        actor=admin_user,
        upload=SimpleUploadedFile("history.csv", content, content_type="text/csv"),
    )
    job.status = "QUEUED"
    job.save()
    execute_import(job.pk)
    second.refresh_from_db()
    assert second.order_id
    assert SalesOrder.objects.filter(external_order_no="2002").count() == 1
    assert (
        ExternalFactApplication.objects.get(external_key="2002").result
        == ExternalFactApplication.Result.LINKED
    )
    assert StockMovement.objects.count() == movement_count


def test_shipping_contract_and_withdrawal_never_apply_business_actions(admin_user, shop):
    start = datetime(2020, 1, 1, tzinfo=UTC)
    connection = ready_connection(admin_user, shop, start)
    base = platform_data(
        "3001",
        1600000000,
        1600000010,
        consign_time=1600000005,
        consign_type=1,
        waybill_no="SF001",
        express_code="shunfeng",
        express_name="顺丰",
    )
    row = store_order(connection, base)
    assert row.snapshot["consign_time"] == 1600000005
    assert not row.contract_issues
    store_order(
        connection,
        platform_data(
            "3001",
            1600000000,
            1600000020,
            consign_time=0,
            waybill_no="",
            express_code="",
            express_name="",
        ),
    )
    row.refresh_from_db()
    assert row.snapshot["waybill_no"] == "SF001"
    assert any("撤回字段" in issue for issue in row.contract_issues)
    application = ExternalFactApplication.objects.get(fact_type="ORDER")
    assert application.result == "NEEDS_REVIEW"
    assert not Shipment.objects.exists()
    assert not MoneyEntry.objects.exists()
    assert StockMovement.objects.count() == 0

    unknown = store_order(
        connection,
        platform_data(
            "3002",
            1600000000,
            1600000030,
            consign_time=1600000025,
            consign_type=99,
        ),
    )
    assert any("未知发货方式" in issue for issue in unknown.contract_issues)


def test_independent_refunds_version_and_remain_reconciliation_only(admin_user, shop):
    start = datetime(2020, 1, 1, tzinfo=UTC)
    connection = ready_connection(admin_user, shop, start)
    store_order(connection, platform_data("4001", 1600000000, 1600000010))
    first = {
        "order_no": "4001",
        "refund_no": "R-1",
        "update_time": 1600000020,
        "refund_type": 2,
        "refund_status": 1,
        "refund_amount": 5000,
        "waybill_no": "YT001",
        "express_code": "yuantong",
        "refund_reason": "不需要了",
    }
    refund = store_refund(connection, first)
    store_refund(connection, {**first, "update_time": 1600000030, "refund_status": 2})
    store_refund(connection, {**first, "update_time": 1600000025, "refund_status": 9})
    refund.refresh_from_db()
    assert PlatformRefund.objects.count() == 1
    assert refund.snapshot["refund_status"] == 2
    assert refund.revisions.count() == 2
    assert refund.snapshot["waybill_no"] == "YT001"
    assert not MoneyEntry.objects.exists()
    assert not Shipment.objects.exists()


def test_expired_generation_cannot_write_or_advance_cursor(admin_user, shop):
    start = datetime(2020, 1, 1, tzinfo=UTC)
    connection = ready_connection(admin_user, shop, start)
    run = queue_sync(connection)
    updated = run.window_start + 10
    client = MagicMock()

    def steal_lease(*args, **kwargs):
        SyncRun.objects.filter(pk=run.pk).update(
            lease_owner="new-worker",
            lease_generation=F("lease_generation") + 1,
            lease_expires_at=timezone.now() + timedelta(minutes=5),
        )
        return {"list": [platform_data("5001", updated, updated)]}

    client.call.side_effect = steal_lease
    execute_sync(run.pk, client, worker_id="old-worker")
    run.refresh_from_db()
    assert run.status == "RUNNING" and run.lease_owner == "new-worker"
    assert not PlatformOrder.objects.exists()
    assert connection.cursor == 0

    SyncRun.objects.filter(pk=run.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
    client.call.side_effect = None
    client.call.return_value = {"list": [platform_data("5001", updated, updated)]}
    execute_sync(run.pk, client, worker_id="recovered-worker")
    run.refresh_from_db()
    connection.refresh_from_db()
    assert run.status == "DONE"
    assert PlatformOrder.objects.count() == 1
    assert connection.cursor == run.window_end


def test_scheduler_reclaims_expired_run(admin_user, shop):
    connection = ready_connection(admin_user, shop, datetime(2020, 1, 1, tzinfo=UTC))
    run = queue_sync(connection)
    SyncRun.objects.filter(pk=run.pk).update(
        status="RUNNING",
        lease_owner="dead-worker",
        lease_generation=1,
        lease_expires_at=timezone.now() - timedelta(seconds=1),
    )
    with patch("app.integrations.tasks.enqueue") as enqueue:
        poll_orders()
    run.refresh_from_db()
    assert run.status == "RETRY" and not run.lease_owner
    enqueue.assert_called_once()


def test_t4_backfill_is_idempotent_and_uses_connection_audit(admin_user, shop):
    connection = Connection.objects.create(shop=shop, actor=admin_user, seller_id="1234")
    from app.common.business import record_event

    record_event(admin_user, "xgj.connected", connection)
    PlatformOrder.objects.create(
        connection=connection,
        external_order_no="6001",
        source_updated=1600000010,
        snapshot=platform_data("6001", 1600000000, 1600000010),
    )
    migration = import_module(
        "app.integrations.migrations.0005_externalfactapplication_platformrefund_and_more"
    )
    migration.backfill_sync_boundaries(apps, None)
    migration.backfill_sync_boundaries(apps, None)
    connection.refresh_from_db()
    assert connection.sync_start_at
    assert ExternalFactApplication.objects.filter(external_key="6001").count() == 1
