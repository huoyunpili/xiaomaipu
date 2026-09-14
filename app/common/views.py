from django.contrib.auth.decorators import login_required
from django.db import DatabaseError, connection
from django.db.models import F, Q
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from app.accounts.policies import require_admin
from app.audit.models import AuditEvent
from app.catalog.models import SKU
from app.contacts.models import Customer
from app.importing.models import ImportJob
from app.integrations.models import Connection, PlatformOrder
from app.operations.projections import bottlenecks
from app.orders.models import ReturnReceipt, SalesOrder
from app.procurement.models import Purchase
from app.shops.models import SalesChannel, Shop

from .release import local_release_status


@require_GET
def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except DatabaseError:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


@login_required
@require_GET
def dashboard(request):
    active_bottlenecks = bottlenecks()
    platform_connection = Connection.objects.first()
    platform_orders = (
        PlatformOrder.objects.exclude(scope_status=PlatformOrder.Scope.HISTORICAL)
        .filter(needs_review=True)
        .order_by("-source_created_at", "-source_updated")
    )
    return render(
        request,
        "dashboard.html",
        {
            "shop": Shop.objects.filter(is_active=True).first(),
            "channel_count": SalesChannel.objects.filter(is_active=True).count(),
            "sku_count": SKU.objects.filter(is_active=True).count(),
            "pending_returns": ReturnReceipt.objects.filter(status="INSPECTION").select_related(
                "reservation__item__order"
            )[:8],
            "pending_purchases": Purchase.objects.filter(closed=False).filter(
                quantity__gt=F("received_qty") + F("direct_qty") + F("cancelled_qty")
            )[:8],
            "risk_customers": Customer.objects.filter(suggestion="PENDING")[:8],
            "import_failures": ImportJob.objects.filter(status="FAILED")[:8],
            "platform_pending": platform_orders.count(),
            "platform_pending_orders": platform_orders[:5],
            "platform_connection": platform_connection,
            "refund_orders": SalesOrder.objects.filter(
                received_fen__gt=F("amount_fen") - F("amount_reduction_fen") + F("refunded_fen")
            )[:8],
            "missing_evidence": SalesOrder.objects.filter(
                status__in=["CONFIRMED", "PARTIAL", "SHIPPED"]
            ).exclude(videos__active=True)[:8],
            "open_orders": SalesOrder.objects.select_related("channel").filter(
                Q(status__in=["CONFIRMED", "PARTIAL", "SHIPPED"])
                | Q(
                    status="COMPLETED",
                    received_fen__lt=F("amount_fen")
                    - F("amount_reduction_fen")
                    + F("refunded_fen"),
                )
            )[:6],
            "bottleneck_count": len(active_bottlenecks),
            "bottleneck_preview": active_bottlenecks[:6],
            "local_release": local_release_status(),
        },
    )


@require_GET
def health_live(request):
    return JsonResponse({"status": "ok"})


@require_GET
def health_ready(request):
    return health(request)


@login_required
@require_GET
def audit_list(request):
    require_admin(request.user)
    return render(
        request, "audit.html", {"events": AuditEvent.objects.select_related("actor")[:100]}
    )


def forbidden(request, exception=None):
    return render(request, "error.html", {"message": "你没有权限进行此操作。"}, status=403)


def not_found(request, exception=None):
    return render(request, "error.html", {"message": "页面不存在，请返回工作台。"}, status=404)


def server_error(request):
    return render(request, "error.html", {"message": "暂时无法完成操作，请稍后重试。"}, status=500)
