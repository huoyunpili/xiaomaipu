import hashlib
import hmac
import json
import time
from datetime import UTC, date, datetime

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from app.accounts.policies import require_admin, require_operator
from app.catalog.models import SKU
from app.common.business import BusinessError, record_event
from app.common.forms import to_fen
from app.inventory.models import StockLot

from .client import APIError, signature
from .models import Connection, ExternalFactApplication, PlatformOrder, PushNotice
from .services import (
    BEIJING,
    confirm_sync_start,
    connect,
    convert_order,
    queue_sync,
    refresh_order,
    refresh_refund,
)
from .tasks import enqueue


@login_required
@require_GET
def home(request):
    connection = Connection.objects.first()
    end = connection.service_info.get("valid_end_time") if connection else None
    valid_until = (
        datetime.fromtimestamp(end, UTC) if type(end) is int and 0 < end < 10**10 else None
    )
    orders = PlatformOrder.objects.select_related("order")
    if request.GET.get("pending"):
        orders = orders.filter(needs_review=True)
    return render(
        request,
        "integrations/home.html",
        {
            "connection": connection,
            "valid_until": valid_until,
            "runs": connection.runs.all()[:10] if connection else [],
            "rows": Paginator(orders, 30).get_page(request.GET.get("page")),
            "failed_notices": PushNotice.objects.filter(status="FAILED").count(),
            "scope_counts": {
                scope: PlatformOrder.objects.filter(scope_status=scope).count()
                for scope in PlatformOrder.Scope.values
            },
            "application_review_count": ExternalFactApplication.objects.filter(
                result=ExternalFactApplication.Result.NEEDS_REVIEW
            ).count(),
            "sync_start_local": connection.sync_start_at.astimezone(BEIJING).date()
            if connection and connection.sync_start_at
            else None,
        },
    )


@login_required
@require_POST
def operate(request, operation):
    require_admin(request.user)
    try:
        if operation == "connect":
            connect(actor=request.user)
            messages.success(request, "授权店铺验证成功。")
        else:
            connection = get_object_or_404(Connection)
            if operation in ("sync", "rescan"):
                enqueue(queue_sync(connection, full=operation == "rescan"))
                messages.success(request, "已提交同步任务，可刷新查看结果。")
            elif operation == "confirm-start":
                try:
                    start_date = date.fromisoformat(request.POST.get("sync_start_date", ""))
                except ValueError as exc:
                    raise BusinessError("请选择有效的自动同步起始日。") from exc
                confirm_sync_start(actor=request.user, connection=connection, start_date=start_date)
                messages.success(request, "自动同步起始日已固定。")
            elif operation in ("enable", "disable"):
                if operation == "enable" and not connection.sync_start_at:
                    raise BusinessError("请先确认自动同步起始日。")
                connection.enabled = operation == "enable"
                connection.actor = request.user
                connection.save()
                record_event(request.user, f"xgj.{operation}", connection)
            elif operation == "retry-notices":
                PushNotice.objects.filter(connection=connection, status="FAILED").update(
                    status="PENDING", attempts=0, next_attempt=None
                )
                messages.success(request, "失败通知将在下一次后台调度时重试。")
            else:
                raise BusinessError("不支持的操作。")
    except (APIError, BusinessError) as exc:
        messages.error(request, str(exc))
    return redirect("xgj-home")


class ConvertForm(forms.Form):
    sku = forms.ModelChoiceField(label="本地商品", queryset=SKU.objects.filter(is_active=True))
    lot = forms.ModelChoiceField(
        label="实际实物组", queryset=StockLot.objects.all(), required=False
    )
    quantity = forms.IntegerField(label="核对销售数量", min_value=1, max_value=1000000)
    price = forms.DecimalField(
        label="核对成交单价（元）", min_value=0, max_digits=12, decimal_places=2
    )
    confirmed = forms.BooleanField(label="已核对商品、货况、数量及成交金额，创建草稿后继续处理")


@login_required
@require_http_methods(["GET", "POST"])
def detail(request, row_id):
    row = get_object_or_404(PlatformOrder.objects.select_related("order", "connection"), pk=row_id)
    goods = row.snapshot.get("goods", {})
    form = ConvertForm(request.POST or None, initial={"quantity": goods.get("quantity", 1)})
    if request.method == "POST":
        require_operator(request.user)
        try:
            if request.POST.get("action") == "refresh":
                refresh_order(row)
                return redirect("xgj-detail", row_id=row.pk)
            if request.POST.get("action") == "refund":
                refresh_refund(actor=request.user, row_id=row.pk)
                return redirect("xgj-detail", row_id=row.pk)
            if request.POST.get("action") == "review":
                PlatformOrder.objects.filter(pk=row.pk, source_updated=row.source_updated).update(
                    needs_review=False
                )
                record_event(request.user, "xgj.reviewed", row)
                return redirect("xgj-detail", row_id=row.pk)
            if form.is_valid():
                order = convert_order(
                    actor=request.user,
                    row_id=row.pk,
                    sku_id=form.cleaned_data["sku"].pk,
                    quantity=form.cleaned_data["quantity"],
                    unit_price_fen=to_fen(form.cleaned_data["price"]),
                    selected_lot_id=form.cleaned_data["lot"].pk
                    if form.cleaned_data["lot"]
                    else None,
                )
                return redirect("order-detail", order_id=order.pk)
        except (APIError, BusinessError) as exc:
            form.add_error(None, str(exc))
    deadline = row.refund_snapshot.get("timeout_time")
    refund_deadline = (
        datetime.fromtimestamp(deadline, UTC)
        if type(deadline) is int and 0 < deadline < 10**10
        else None
    )
    return render(
        request,
        "integrations/detail.html",
        {
            "row": row,
            "form": form,
            "refund_deadline": refund_deadline,
            "applications": row.connection.applications.filter(
                fact_type__in=["ORDER", "REFUND", "REFUND_SUMMARY"],
                external_key__in=[
                    row.external_order_no,
                    *row.platform_refunds.values_list("external_refund_no", flat=True),
                ],
            )[:10],
            "platform_refunds": row.platform_refunds.all(),
        },
    )


@csrf_exempt
@require_POST
def webhook(request):
    if len(request.body) > 16384 or not settings.XGJ_APP_SECRET:
        return JsonResponse({"result": "fail"}, status=400)
    try:
        stamp = int(request.GET.get("timestamp", ""))
        if abs(time.time() - stamp) > 300 or request.GET.get("appid") != settings.XGJ_APP_KEY:
            raise ValueError
        expected = signature(settings.XGJ_APP_KEY, settings.XGJ_APP_SECRET, stamp, request.body)
        supplied = request.GET.get("sign", "")
        if not supplied.isascii() or not hmac.compare_digest(expected, supplied):
            raise ValueError
        payload = json.loads(request.body)
        external = payload.get("order_no")
        if not isinstance(external, str) or not external.isdigit() or len(external) > 100:
            raise ValueError
        connection = Connection.objects.get()
    except (
        ValueError,
        AttributeError,
        Connection.DoesNotExist,
        Connection.MultipleObjectsReturned,
    ):
        return JsonResponse({"result": "fail"}, status=403)
    # Commit before acknowledging; Beat drains the durable inbox even if Redis is down.
    with transaction.atomic():
        PushNotice.objects.get_or_create(
            fingerprint=hashlib.sha256(str(connection.pk).encode() + request.body).hexdigest(),
            defaults={"connection": connection, "external_order_no": external},
        )
    return JsonResponse({"result": "success", "msg": "接收成功"})
