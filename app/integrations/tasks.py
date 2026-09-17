import logging
from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .client import APIError, XgjClient
from .models import Connection, PushNotice, SyncRun
from .services import execute_sync, queue_sync, refresh_order, refresh_refund, store_order

logger = logging.getLogger(__name__)


@shared_task(acks_late=True, reject_on_worker_lost=True)
def sync_orders(run_id):
    try:
        execute_sync(run_id)
    except APIError:
        run = SyncRun.objects.get(pk=run_id)
        if run.status == "RETRY":
            sync_orders.apply_async(args=[str(run.pk)], countdown=30 * run.attempts)
        else:
            Connection.objects.filter(pk=run.connection_id).update(enabled=False)
    except Exception:
        # execute_sync only lets the current lease generation mark the run failed.
        raise
    else:
        if SyncRun.objects.filter(pk=run_id, status="DONE").exists():
            enrich_synced_orders.delay(str(run_id))


@shared_task(acks_late=True, reject_on_worker_lost=True)
def enrich_synced_orders(run_id, attempt=0):
    """List responses omit images, shipping times and refund type; fetch their details."""
    from app.common.business import BusinessError
    from app.workbench.models import Trade

    run = SyncRun.objects.select_related("connection__actor").get(pk=run_id)
    if run.status != "DONE":
        return
    failures = 0
    trades = Trade.objects.filter(
        platform__connection=run.connection,
        platform__source_updated__gte=run.window_start,
        platform__source_updated__lte=run.window_end,
    ).select_related("platform__connection")
    for trade in trades.iterator():
        try:
            row = refresh_order(trade.platform)
            if (
                row.snapshot.get("refund_status") in (1, 2, 3, 5, 6, 8)
                or row.snapshot.get("order_status") == 23
            ):
                refresh_refund(actor=run.connection.actor, row_id=row.pk)
        except (APIError, BusinessError):
            failures += 1
    if failures:
        logger.warning("order_detail_enrichment_incomplete", extra={"failed_count": failures})
        if attempt < 2:
            enrich_synced_orders.apply_async(args=[str(run.pk), attempt + 1], countdown=60)


def enqueue(run):
    try:
        sync_orders.delay(str(run.pk))
    except Exception:
        SyncRun.objects.filter(pk=run.pk).update(
            status="FAILED",
            error="后台队列不可用，请恢复任务服务后重试。",
            lease_owner="",
            lease_expires_at=None,
        )


@shared_task
def poll_orders():
    stale = timezone.now() - timedelta(minutes=15)
    reclaimed_connections = set()
    expired = list(
        SyncRun.objects.filter(status="RUNNING")
        .filter(
            Q(lease_expires_at__lte=timezone.now())
            | Q(lease_expires_at__isnull=True, updated_at__lt=stale)
        )
        .values_list("pk", flat=True)
    )
    for run_id in expired:
        with transaction.atomic():
            run = SyncRun.objects.select_for_update().filter(pk=run_id, status="RUNNING").first()
            if not run:
                continue
            if run.lease_expires_at and run.lease_expires_at > timezone.now():
                continue
            run.status = "RETRY"
            run.lease_owner = ""
            run.lease_expires_at = None
            run.error = "执行者心跳失效，任务已由本地调度器安全接管。"
            run.save()
            reclaimed_connections.add(run.connection_id)
        enqueue(run)
    for connection in Connection.objects.filter(enabled=True):
        if connection.pk in reclaimed_connections:
            continue
        if connection.actor.is_active and connection.actor.is_shop_admin:
            try:
                enqueue(queue_sync(connection))
            except APIError as exc:
                Connection.objects.filter(pk=connection.pk).update(enabled=False, error=str(exc))
    process_notices()


@shared_task(acks_late=True, reject_on_worker_lost=True)
def process_notices():
    ids = list(
        PushNotice.objects.filter(status="PENDING")
        .filter(Q(next_attempt__isnull=True) | Q(next_attempt__lte=timezone.now()))
        .order_by("created_at")
        .values_list("pk", flat=True)[:50]
    )
    for notice_id in ids:
        with transaction.atomic():
            notice = (
                PushNotice.objects.select_for_update(skip_locked=True).filter(pk=notice_id).first()
            )
            if not notice or notice.status != "PENDING":
                continue
            try:
                data = XgjClient().call(
                    "detail",
                    {"order_no": notice.external_order_no},
                    seller=notice.connection.seller_id,
                )
                if data.get("order_no") != notice.external_order_no:
                    raise APIError("平台返回订单号与通知不一致。")
                store_order(notice.connection, data)
                notice.status = "DONE"
                notice.error = ""
            except APIError as exc:
                notice.attempts += 1
                notice.error = str(exc)
                notice.next_attempt = timezone.now() + timedelta(minutes=1)
                if not exc.retryable or notice.attempts >= 3:
                    notice.status = "FAILED"
            notice.save()
