from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .client import APIError, XgjClient
from .models import Connection, PushNotice, SyncRun
from .services import execute_sync, queue_sync, store_order


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
        SyncRun.objects.filter(pk=run_id).update(
            status="FAILED", error="同步意外中断，请重试；已有订单会自动去重。"
        )
        raise


def enqueue(run):
    try:
        sync_orders.delay(str(run.pk))
    except Exception:
        SyncRun.objects.filter(pk=run.pk).update(
            status="FAILED", error="后台队列不可用，请恢复任务服务后重试。"
        )


@shared_task
def poll_orders():
    stale = timezone.now() - timedelta(minutes=15)
    SyncRun.objects.filter(status__in=["QUEUED", "RUNNING", "RETRY"], updated_at__lt=stale).update(
        status="FAILED", error="任务超时，已释放同步占用，可重新同步。"
    )
    for connection in Connection.objects.filter(enabled=True):
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
