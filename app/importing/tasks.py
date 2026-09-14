from celery import shared_task

from .models import ImportJob
from .services import execute_import


@shared_task(acks_late=True, reject_on_worker_lost=True)
def run_import(job_id):
    try:
        execute_import(job_id)
    except Exception:
        ImportJob.objects.filter(pk=job_id).update(
            status="FAILED", error="处理意外中断，可重试；已成功的行不会重复创建。"
        )
        raise
