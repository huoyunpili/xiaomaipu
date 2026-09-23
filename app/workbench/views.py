import csv
import io
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import (
    Case,
    DateTimeField,
    F,
    IntegerField,
    OuterRef,
    Q,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from app.accounts.policies import require_admin
from app.common.business import BusinessError, record_event
from app.integrations.client import APIError
from app.integrations.models import Connection
from app.integrations.services import refresh_order, refresh_refund
from app.shops.models import Shop

from .exports import MAX_PNG_BYTES, private_path, save_image
from .forms import ProductForm, ReferenceForm, RetentionForm, TradeForm
from .models import (
    ExportBatch,
    ExportImage,
    ProductCost,
    SupplierDispatch,
    Trade,
    WorkspaceSettings,
)
from .product_images import trade_image
from .repayment import PERIODS, due_rows, filter_due
from .services import (
    batch_signature,
    create_batches,
    invalidate_batches,
    supplier_choices,
    update_product,
)


def active_shop():
    return Shop.objects.filter(is_active=True).first()


def orders():
    return Trade.objects.filter(shop=active_shop()).select_related(
        "product", "platform__connection", "shop__workspacesettings"
    )


@login_required
@require_GET
def product_image(request, pk):
    trade = get_object_or_404(orders(), pk=pk)
    if not trade.image and not trade.platform_id:
        raise Http404
    try:
        path, mime = trade_image(trade)
        response = FileResponse(path.open("rb"), content_type=mime)
    except (APIError, OSError, ValueError):
        unavailable = HttpResponse("商品图片暂不可用，请稍后刷新。", status=503)
        unavailable["Cache-Control"] = "no-store"
        return unavailable
    response["Cache-Control"] = "private, max-age=3600"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def reference_days():
    config = WorkspaceSettings.objects.filter(shop=active_shop()).first()
    return config.reference_days if config else 10


def date_range(params, default="month"):
    today = timezone.localdate()
    preset = params.get("range", default)
    start, end = today.replace(day=1), today
    if preset == "today":
        start = today
    elif preset == "week":
        start = today - timedelta(days=6)
    elif preset == "last_month":
        end = today.replace(day=1) - timedelta(days=1)
        start = end.replace(day=1)
    elif preset == "custom":
        try:
            start, end = (
                date.fromisoformat(params.get("start", "")),
                date.fromisoformat(params.get("end", "")),
            )
        except ValueError as exc:
            raise BusinessError("请选择有效的开始与结束日期。") from exc
    if start > end:
        raise BusinessError("开始日期不能晚于结束日期。")
    return start, end


def profit_summary(trades):
    good = [t for t in trades if t.profit_fen is not None]
    missing = [t for t in trades if t.unit_cost_fen is None]
    return {
        "count": len(good),
        "sales": sum(t.paid_fen for t in good),
        "cost": sum(t.cost_fen for t in good),
        "fee": sum(t.fee_fen for t in good),
        "profit": sum(t.profit_fen for t in good),
        "missing": len(missing),
        "missing_sales": sum(t.paid_fen for t in missing),
        "loss": sum(t.loss_fen for t in good),
    }


@login_required
@require_GET
def dashboard(request):
    rows = list(orders())
    latest_batches: dict[str, ExportBatch] = {}
    for batch in ExportBatch.objects.filter(shop=active_shop(), kind="shipping").order_by(
        "-created_at", "-pk"
    ):
        # A newer shipping export supersedes earlier reminders for this supplier.
        latest_batches.setdefault(batch.supplier, batch)
    unrecovered_returns = [
        t
        for t in rows
        if t.status in ("REFUNDING", "REFUNDED") and t.refund_type == 2 and not t.recovered_at
    ]
    now = timezone.now()
    today = timezone.localdate(now)
    due = {period: due_rows(rows, period, now) for period in PERIODS}
    completed_today = [
        t
        for t in rows
        if t.status == "COMPLETED"
        and (t.paid_at or t.source == "BILL_IMPORT")
        and t.completed_at
        and timezone.localdate(t.completed_at) == today
    ]
    completed_today_amount = sum(t.paid_fen for t in completed_today)
    today_due_amount = sum(t.guarantee_fen for t in due["today"])
    summary = profit_summary(
        [
            t
            for t in rows
            if t.paid_at
            and timezone.localdate(t.paid_at) == today
            and t.status in ("SHIPPING", "PENDING", "COMPLETED")
        ]
    )
    return render(
        request,
        "workbench/dashboard.html",
        {
            "shipping": sum(t.status == "SHIPPING" for t in rows),
            "refunds": sum(t.status == "REFUNDING" for t in rows),
            "recovery_count": len(unrecovered_returns),
            "completed_today_amount": completed_today_amount,
            "completed_today_count": len(completed_today),
            "today_repayment_amount": completed_today_amount + today_due_amount,
            "today_repayment_count": len(completed_today) + len(due["today"]),
            "today_due_count": len(due["today"]),
            "today_date": today.isoformat(),
            "soon_start": today + timedelta(days=1),
            "soon_end": today + timedelta(days=3),
            "unknown_due_count": len(due["unknown"]),
            "unknown_due_amount": sum(t.guarantee_fen for t in due["unknown"]),
            "guarantee": sum(t.guarantee_fen for t in rows),
            "review_count": sum(t.status == "REVIEW" for t in rows),
            "review_paid_fen": sum(t.paid_fen for t in rows if t.status == "REVIEW" and t.paid_at),
            "guarantee_count": sum(bool(t.guarantee_fen) for t in rows),
            "reference_days": reference_days(),
            "overdue_amount": sum(t.guarantee_fen for t in due["overdue"]),
            "summary": summary,
            "risk": sum(t.guarantee_fen for t in rows if t.status == "REFUNDING"),
            "recovery": (
                None
                if any(t.cost_fen is None for t in unrecovered_returns)
                else sum(t.cost_fen for t in unrecovered_returns)
            ),
            "today_due": today_due_amount,
            "soon_due": sum(t.guarantee_fen for t in due["soon"]),
            "overdue": due["overdue"],
            "connection": Connection.objects.filter(shop=active_shop()).first(),
            "stale_batches": [batch for batch in latest_batches.values() if batch.stale][:5],
            "waiting": sorted(
                [t for t in rows if t.status == "SHIPPING"], key=lambda t: t.paid_at or t.created_at
            )[:5],
        },
    )


@login_required
@require_GET
def listing(request, area="all"):
    params = request.GET
    if area == "shipping":
        # Retired filters in old URLs must not silently hide unshipped orders.
        params = request.GET.copy()
        for key in list(params):
            if key not in ("page", "export"):
                del params[key]
    qs = orders().exclude(status="UNPAID")
    refund_view = params.get("refund_view", "processing")
    if params.get("history") == "1":
        refund_view = "all"
    if refund_view not in ("processing", "recovery", "completed", "all"):
        refund_view = "processing"
    refund_counts = (
        {
            "processing": qs.filter(status="REFUNDING").count(),
            "recovery": qs.filter(
                status__in=["REFUNDING", "REFUNDED"], refund_type=2, recovered_at__isnull=True
            ).count(),
            "completed": qs.filter(status="REFUNDED").count(),
        }
        if area == "refunds"
        else {}
    )
    counts = [
        (code, label, qs.filter(status=code).count())
        for code, label in Trade.Status.choices
        if code != "UNPAID"
    ]
    state = params.get("status", "")
    if area == "shipping":
        qs = qs.filter(status="SHIPPING")
    elif area == "pending":
        qs = qs.filter(status="PENDING")
    elif area == "refunds":
        if refund_view == "processing":
            qs = qs.filter(status="REFUNDING")
        elif refund_view == "recovery":
            qs = qs.filter(
                status__in=["REFUNDING", "REFUNDED"], refund_type=2, recovered_at__isnull=True
            )
        elif refund_view == "completed":
            qs = qs.filter(status="REFUNDED")
        else:
            qs = qs.filter(status__in=["REFUNDING", "REFUNDED"])
    elif state:
        qs = qs.filter(status=state)
    if params.get("guarantee") == "1":
        qs = qs.filter(status__in=["SHIPPING", "PENDING", "REFUNDING"], paid_at__isnull=False)
    if params.get("repayment") == "today":
        now = timezone.now()
        pending_ids = filter_due(orders(), "today", reference_days(), now).values("pk")
        qs = qs.filter(
            (Q(paid_at__isnull=False) | Q(source="BILL_IMPORT"))
            & Q(status="COMPLETED", completed_at__date=timezone.localdate(now))
            | Q(pk__in=pending_ids)
        )
    if params.get("due") in PERIODS:
        qs = filter_due(qs, params["due"], reference_days(), timezone.now())
    if params.get("supplier"):
        qs = qs.filter(supplier=params["supplier"])
    if params.get("q"):
        q = params["q"]
        qs = qs.filter(
            Q(number__icontains=q)
            | Q(title__icontains=q)
            | Q(spec__icontains=q)
            | Q(receiver__icontains=q)
        )
    if params.get("start") or params.get("end"):
        try:
            start, end = date_range(
                {"range": "custom", "start": params.get("start", ""), "end": params.get("end", "")}
            )
            field = {"paid": "paid_at", "completed": "completed_at"}.get(
                params.get("date_field"), "ordered_at"
            )
            qs = qs.filter(**{f"{field}__date__range": (start, end)})
        except BusinessError as exc:
            messages.error(request, str(exc))
            qs = qs.none()
    if area == "pending" and params.get("sort") == "reference":
        qs = qs.order_by("shipped_at", "id")
    elif area in ("shipping", "pending", "refunds"):
        qs = qs.order_by(Coalesce("paid_at", "created_at"), "id")
    else:
        todo = ["SHIPPING", "PENDING", "REFUNDING", "REVIEW"]
        groups = todo + ["COMPLETED", "REFUNDED", "CLOSED"]
        qs = qs.annotate(
            group_rank=Case(
                *[When(status=state, then=Value(index)) for index, state in enumerate(groups)],
                default=Value(99),
                output_field=IntegerField(),
            ),
            waiting_time=Case(
                When(status__in=todo, then=Coalesce("paid_at", "created_at")),
                output_field=DateTimeField(),
            ),
            ended_time=Case(
                When(status__in=["COMPLETED", "REFUNDED", "CLOSED"], then=F("status_changed_at")),
                output_field=DateTimeField(),
            ),
        ).order_by("group_rank", "waiting_time", "-ended_time", "id")
    if params.get("export") == "csv":
        return csv_response(
            ["订单号", "状态", "商品", "规格", "数量", "实付分", "供应商"],
            [
                [
                    t.number,
                    t.get_status_display(),
                    t.title,
                    t.spec,
                    t.quantity,
                    t.paid_fen,
                    t.supplier,
                ]
                for t in qs
            ],
            "orders.csv",
        )
    return render(
        request,
        "workbench/list.html",
        {
            "area": area,
            "refund_view": refund_view,
            "refund_counts": refund_counts,
            "due_label": PERIODS.get(params.get("due", ""), ""),
            "title": {
                "shipping": "待发货",
                "pending": "待完成",
                "refunds": "退款售后",
                "all": "全部订单",
            }[area],
            "rows": Paginator(qs, 30).get_page(params.get("page")),
            "counts": counts,
            "suppliers": orders()
            .exclude(supplier="")
            .values_list("supplier", flat=True)
            .distinct(),
            "connection": Connection.objects.filter(shop=active_shop()).first(),
            "batches": ExportBatch.objects.filter(
                shop=active_shop(), kind="refund" if area == "refunds" else "shipping"
            ).order_by("-created_at")[:20],
            "query": urlencode({k: v for k, v in params.items() if k not in ("page", "export")}),
        },
    )


@login_required
@require_POST
def recovery(request, pk):
    require_admin(request.user)
    trade = get_object_or_404(orders(), pk=pk)
    choice = request.POST.get("recovered")
    if choice not in ("yes", "no"):
        return JsonResponse({"error": "请选择是否已追回货款。"}, status=400)
    with transaction.atomic():
        trade = Trade.objects.select_for_update().get(pk=trade.pk)
        if trade.status not in ("REFUNDING", "REFUNDED"):
            return JsonResponse(
                {"error": "订单状态已变化，当前不是退款订单，请刷新后查看。"}, status=409
            )
        before = bool(trade.recovered_at)
        recovered = choice == "yes"
        if before != recovered:
            trade.recovered_at = timezone.now() if recovered else None
            trade.save(update_fields=["recovered_at", "updated_at"])
            invalidate_batches(trade)
            record_event(
                request.user,
                "workbench.recovery_updated",
                trade,
                before=before,
                recovered=recovered,
            )
    return JsonResponse(
        {
            "recovered": recovered,
            "recovery_count": orders()
            .filter(status__in=["REFUNDING", "REFUNDED"], refund_type=2, recovered_at__isnull=True)
            .count(),
        }
    )


@login_required
@require_http_methods(["GET", "POST"])
def detail(request, pk):
    trade = get_object_or_404(orders(), pk=pk)
    form = TradeForm(
        request.POST or None,
        initial={
            "supplier": trade.supplier,
            "spec": trade.spec,
            "receiver": trade.receiver,
            "phone": trade.phone,
            "address": trade.address,
            "note": trade.note,
            "supplier_wechat": trade.supplier_wechat,
            "loss": Decimal(trade.loss_fen) / 100,
            "refund_note": trade.refund_note,
            "refund_waybill": trade.refund_waybill,
            "recovered": bool(trade.recovered_at),
        },
    )
    if request.method == "POST" and form.is_valid():
        require_admin(request.user)
        with transaction.atomic():
            trade = Trade.objects.select_for_update().get(pk=trade.pk)
            data = form.cleaned_data
            if data["recovered"] and trade.status not in ("REFUNDING", "REFUNDED"):
                form.add_error("recovered", "仅退款处理中或已退款订单可以确认供应商追回结果。")
            elif data.get("loss") and trade.status not in ("REFUNDING", "REFUNDED"):
                form.add_error("loss", "仅售后订单填写实际运费损失。")
            else:
                before = {
                    "cost": trade.unit_cost_fen,
                    "loss": trade.loss_fen,
                    "recovered": bool(trade.recovered_at),
                }
                supplier_changed = (
                    data["supplier"] != trade.supplier
                    or data["supplier_wechat"] != trade.supplier_wechat
                )
                for field in (
                    "supplier",
                    "supplier_wechat",
                    "spec",
                    "receiver",
                    "phone",
                    "address",
                    "note",
                    "refund_note",
                    "refund_waybill",
                ):
                    setattr(trade, field, data[field])
                trade.supplier_override = trade.supplier_override or supplier_changed
                loss = int((data.get("loss") or 0) * 100)
                if loss != trade.loss_fen:
                    trade.loss_at = timezone.now()
                trade.loss_fen = loss
                trade.recovered_at = (
                    (trade.recovered_at or timezone.now()) if data["recovered"] else None
                )
                if data.get("correction") is not None:
                    trade.unit_cost_fen = int(data["correction"] * 100)
                trade.save()
                invalidate_batches(trade)
                record_event(
                    request.user,
                    "workbench.order_edited",
                    trade,
                    before=before,
                    cost=trade.unit_cost_fen,
                    loss=trade.loss_fen,
                    recovered=bool(trade.recovered_at),
                    reason=data["reason"],
                )
                messages.success(request, "订单补充信息已保存。")
                return redirect("wb-detail", pk=trade.pk)
    return render(
        request,
        "workbench/detail.html",
        {
            "trade": trade,
            "form": form,
            "supplier_choices": supplier_choices(trade.shop),
            "legacy_supplier_dispatch": SupplierDispatch.objects.filter(trade=trade).first(),
            "legacy_supplier_videos": list(trade.supplier_videos.order_by("created_at")),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def costs(request):
    form = ProductForm(request.POST or None)
    wants_json = "application/json" in request.headers.get("Accept", "")
    if request.method == "POST" and wants_json:
        require_admin(request.user)
        if not form.is_valid():
            return JsonResponse({"errors": form.errors.get_json_data()}, status=400)
    if request.method == "POST" and form.is_valid():
        require_admin(request.user)
        product = get_object_or_404(ProductCost, pk=request.POST.get("product"), shop=active_shop())
        update_product(
            product,
            int(form.cleaned_data["cost"] * 100)
            if form.cleaned_data["cost"] is not None
            else product.unit_fen,
            form.cleaned_data["supplier"],
            request.user,
            supplier_wechat=form.cleaned_data["supplier_wechat"],
            shipping_note=form.cleaned_data["shipping_note"],
            condition=form.cleaned_data["condition"] if "condition" in request.POST else None,
        )
        if wants_json:
            product.refresh_from_db()
            return JsonResponse(
                {
                    "saved": {
                        "cost": format(Decimal(product.unit_fen) / 100, ".2f")
                        if product.unit_fen is not None
                        else "",
                        "supplier": product.supplier,
                        "supplier_wechat": product.supplier_wechat,
                        "shipping_note": product.shipping_note,
                        "condition": product.condition,
                    },
                    "version": product.version,
                    "display_condition": product.display_condition,
                    "revisions": [
                        f"版本 {revision.version} · ¥{Decimal(revision.unit_fen) / 100:.2f} · "
                        f"{timezone.localtime(revision.effective_at):%Y-%m-%d %H:%M:%S} 生效"
                        for revision in product.revisions.all()
                    ],
                    "supplier_choices": supplier_choices(product.shop),
                }
            )
        messages.success(request, "成本已保存，同类型缺成本订单已自动补齐；已有快照保持原值。")
        return redirect("wb-costs")
    related_orders = Trade.objects.filter(product=OuterRef("pk"), shop=active_shop()).order_by(
        "-paid_at", "pk"
    )
    paid_orders = related_orders.filter(
        Q(paid_at__isnull=False) | Q(source="BILL_IMPORT", status="COMPLETED")
    ).exclude(status="UNPAID")
    return render(
        request,
        "workbench/costs.html",
        {
            "products": ProductCost.objects.filter(shop=active_shop())
            .annotate(
                image_trade_id=Coalesce(
                    Subquery(related_orders.exclude(image="").values("pk")[:1]),
                    Subquery(related_orders.filter(platform__isnull=False).values("pk")[:1]),
                ),
                example_trade_id=Subquery(related_orders.values("pk")[:1]),
                sale_trade_id=Subquery(paid_orders.values("pk")[:1]),
                sale_paid_fen=Subquery(paid_orders.values("paid_fen")[:1]),
                sale_quantity=Subquery(paid_orders.values("quantity")[:1]),
                sale_paid_at=Subquery(paid_orders.values("paid_at")[:1]),
            )
            .prefetch_related("revisions")
            .order_by("title", "spec"),
            "form": form,
            "supplier_choices": supplier_choices(active_shop()),
        },
    )


@login_required
@require_GET
def profits(request):
    params = request.GET.dict()
    if not any(k != "export" for k in params):
        params = {**request.session.get("wb_profit_filter", {}), **params}
    mode = params.get("mode", "actual")
    if mode not in ("expected", "actual"):
        mode = "actual"
    params["mode"] = mode
    params.setdefault("range", "today" if mode == "expected" else "month")
    try:
        start, end = date_range(params, "today" if mode == "expected" else "month")
    except BusinessError as exc:
        return render(
            request,
            "workbench/profits.html",
            {"error": str(exc), "params": params, "mode": mode},
            status=400,
        )
    params = {k: v for k, v in params.items() if k != "export"}
    request.session["wb_profit_filter"] = params
    field = "paid_at" if mode == "expected" else "completed_at"
    qs = orders().filter(**{f"{field}__date__range": (start, end)})
    qs = qs.filter(
        status__in=["SHIPPING", "PENDING", "COMPLETED"] if mode == "expected" else ["COMPLETED"]
    )
    rows = list(qs.order_by(field, "number"))
    sorting = params.get("sort", "date")
    if sorting in ("profit_high", "profit_low"):
        direction = -1 if sorting == "profit_high" else 1
        rows.sort(key=lambda t: (t.profit_fen is None, direction * (t.profit_fen or 0), t.number))
    elif sorting == "product":
        rows.sort(key=lambda t: (t.title, t.spec, t.number))
    summary = profit_summary(rows)
    losses = list(orders().filter(loss_at__date__range=(start, end), loss_fen__gt=0))
    risks = list(orders().filter(paid_at__date__range=(start, end), status="REFUNDING"))
    trend: dict[date, int] = {}
    for t in rows:
        day = timezone.localdate(getattr(t, field))
        trend[day] = trend.get(day, 0) + (t.profit_fen or 0)
    peak = max((abs(value) for value in trend.values()), default=0) or 1
    trend_chart = [
        {"day": day, "profit": value, "width": round(abs(value) * 100 / peak, 2)}
        for day, value in sorted(trend.items())
    ]
    if request.GET.get("export"):
        data = [
            [
                t.number,
                t.get_status_display(),
                timezone.localtime(getattr(t, field)).isoformat(),
                t.paid_fen,
                t.quantity,
                t.unit_cost_fen,
                t.cost_fen,
                t.fee_fen,
                t.profit_fen,
                t.loss_fen,
                "纳入利润合计" if t.profit_fen is not None else "待补成本，未纳入合计",
                t.title,
                t.spec,
            ]
            for t in rows
        ]
        data.append(
            [
                "合计（已知成本）",
                "",
                f"{start} 至 {end}",
                summary["sales"],
                "",
                "",
                summary["cost"],
                summary["fee"],
                summary["profit"],
                summary["loss"],
                "",
                "",
                "",
            ]
        )
        data.extend(
            [
                t.number,
                "售后损失（单列）",
                timezone.localtime(t.loss_at).isoformat(),
                "",
                "",
                "",
                "",
                "",
                "",
                t.loss_fen,
                "不计入成交利润",
                t.title,
                t.spec,
            ]
            for t in losses
        )
        return csv_response(
            [
                "订单号",
                "状态",
                "统计时间",
                "实付分",
                "数量",
                "单件成本分",
                "成本分",
                "平台费分",
                "利润分",
                "运费损失分",
                "计算口径",
                "商品",
                "规格",
            ],
            data,
            "profit.csv",
        )
    return render(
        request,
        "workbench/profits.html",
        {
            "rows": rows,
            "summary": summary,
            "mode": mode,
            "params": params,
            "start": start,
            "end": end,
            "trend": sorted(trend.items()),
            "trend_chart": trend_chart,
            "losses": losses,
            "loss_total": sum(t.loss_fen for t in losses),
            "risks": risks,
            "query": urlencode(params),
        },
    )


def csv_response(headers, rows, filename):
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(
            [
                ("'" + v)
                if isinstance(v, str) and v.startswith(("=", "+", "-", "@", "\t", "\r"))
                else v
                for v in row
            ]
        )
    response = HttpResponse("\ufeff" + stream.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Cache-Control"] = "no-store"
    return response


@login_required
@require_POST
def export(request, kind):
    require_admin(request.user)
    if kind not in ("shipping", "refund"):
        return HttpResponse(status=404)
    try:
        selected = list(orders().filter(pk__in=request.POST.getlist("selected")))
        if not selected:
            raise BusinessError("请先选择订单。")
        for t in selected:
            if t.platform:
                refresh_order(t.platform)
                if kind == "refund":
                    refresh_refund(actor=request.user, row_id=t.platform.pk)
            elif kind == "shipping":
                raise BusinessError("历史导入订单请先关联 API 并核验当前状态，再生成发货单。")
        selected = list(orders().filter(pk__in=[t.pk for t in selected]))
        if kind == "shipping" and request.POST.get("supplier_filter") == "1":
            chosen = request.POST.getlist("export_supplier")
            if not chosen:
                raise BusinessError("请至少选择一个供货商。")
            selected = [t for t in selected if t.supplier in chosen]
        batches = create_batches(selected, kind, request.user)
        return render(request, "workbench/export_result.html", {"batches": batches})
    except (BusinessError, APIError, ValueError) as exc:
        messages.error(request, str(exc))
        return redirect("wb-refunds" if kind == "refund" else "wb-shipping")


@login_required
@require_GET
def batch(request, pk):
    from .supplier_views import batch_text

    require_admin(request.user)
    item = get_object_or_404(ExportBatch, pk=pk, shop=active_shop())
    current = {str(t.pk): t for t in orders().filter(pk__in=[r["id"] for r in item.snapshot])}
    changed = [
        r
        for r in item.snapshot
        if r["id"] not in current or batch_signature(current[r["id"]]) != r["signature"]
    ]
    response = render(
        request,
        "workbench/batch.html",
        {
            "batch": item,
            "payload": item.snapshot,
            "changed": changed,
            "stop_shipping": item.kind == "shipping"
            and any(
                row["id"] not in current
                or current[row["id"]].status not in ("PENDING", "COMPLETED")
                for row in changed
            ),
            "legacy_product": any("title" not in row for row in item.snapshot),
            "shipping_text": batch_text(item),
        },
    )
    response["Cache-Control"] = "no-store"
    return response


@login_required
@require_http_methods(["GET", "POST"])
def settings(request):
    require_admin(request.user)
    config, _ = WorkspaceSettings.objects.get_or_create(shop=active_shop())
    form = RetentionForm(
        request.POST
        if request.method == "POST" and request.POST.get("action") != "reference"
        else None,
        initial={"image_retention_days": config.image_retention_days},
    )
    reference_form = ReferenceForm(
        request.POST
        if request.method == "POST" and request.POST.get("action") == "reference"
        else None,
        initial={"reference_days": config.reference_days},
    )
    if reference_form.is_bound and reference_form.is_valid():
        before = config.reference_days
        config.reference_days = reference_form.cleaned_data["reference_days"]
        config.save(update_fields=["reference_days", "updated_at"])
        record_event(
            request.user,
            "workbench.reference_changed",
            config,
            before_days=before,
            reference_days=config.reference_days,
        )
        messages.success(request, "统一回款参考规则已保存。")
        return redirect("wb-settings")
    if form.is_bound and form.is_valid():
        config.image_retention_days = form.cleaned_data["image_retention_days"]
        config.save(update_fields=["image_retention_days", "updated_at"])
        record_event(
            request.user,
            "workbench.retention_changed",
            config,
            retention_days=config.image_retention_days,
        )
        messages.success(request, "图片保留期限已保存；订单与历史批次不会删除。")
        return redirect("wb-settings")
    return render(
        request,
        "workbench/settings.html",
        {
            "retention_form": form,
            "reference_form": reference_form,
        },
    )


@login_required
@require_POST
def batch_image(request, pk):
    require_admin(request.user)
    item = get_object_or_404(ExportBatch, pk=pk, shop=active_shop())
    try:
        page = int(request.POST.get("page", "0"))
        upload = request.FILES.get("image")
        if not upload or upload.size > MAX_PNG_BYTES:
            raise BusinessError("图片缺失或超过 16MB，请重新生成。")
        saved = save_image(item, page, upload.read(MAX_PNG_BYTES + 1))
        response = JsonResponse({"url": reverse("wb-image", args=[saved.pk])})
        response["Cache-Control"] = "no-store"
        return response
    except (BusinessError, ValueError) as exc:
        return JsonResponse(
            {"error": str(exc) if isinstance(exc, BusinessError) else "页码无效。"}, status=400
        )


@login_required
@require_GET
def image_download(request, pk):
    require_admin(request.user)
    item = get_object_or_404(
        ExportImage.objects.select_related("batch"), pk=pk, batch__shop=active_shop()
    )
    try:
        path = private_path(item.storage_name)
        stream = path.open("rb")
    except (OSError, BusinessError) as exc:
        raise Http404("图片已清理或不可用，可从历史批次重新生成。") from exc
    response = FileResponse(
        stream,
        content_type="image/png",
        as_attachment=True,
        filename=f"{item.batch.kind}-{item.page}.png",
    )
    response["Cache-Control"] = "no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
