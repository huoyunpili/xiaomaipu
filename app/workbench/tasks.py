from celery import shared_task

from .exports import cleanup_images


@shared_task
def cleanup_exports():
    return cleanup_images()


@shared_task
def dispatch_supplier_order(pk):
    from .supplier_service import process_dispatch

    process_dispatch(pk)


@shared_task
def poll_supplier_dispatches():
    from datetime import timedelta

    from django.utils import timezone

    from .models import SupplierDispatch
    from .supplier_service import process_dispatch, reconcile_dispatch

    for pk in (
        SupplierDispatch.objects.filter(state="READY")
        .order_by("created_at", "pk")
        .values_list("pk", flat=True)[:20]
    ):
        process_dispatch(pk)
    for pk in (
        SupplierDispatch.objects.filter(
            state__in=["SENDING", "UNKNOWN"], submitted_at__lt=timezone.now() - timedelta(minutes=2)
        )
        .order_by("updated_at", "pk")
        .values_list("pk", flat=True)[:20]
    ):
        reconcile_dispatch(pk)
