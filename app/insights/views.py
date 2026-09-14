from datetime import date

from django import forms
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from app.accounts.policies import require_admin, require_operator
from app.catalog.models import SKU
from app.common.business import record_event
from app.common.form_views import business_form
from app.common.forms import SubmissionForm, money_field, to_fen
from app.common.services import execute_once
from app.shops.models import SalesChannel

from .metrics import active_orders, order_totals, product_metrics
from .models import IntelArticle, IntelSource, MarketEvent, MarketPrice


class PriceForm(SubmissionForm):
    price = money_field("市场参考单价（元）")
    observed_on = forms.DateField(
        label="观察日期", initial=timezone.localdate, widget=forms.DateInput(attrs={"type": "date"})
    )
    source = forms.CharField(label="价格来源或依据", max_length=300)

    def clean_observed_on(self):
        value = self.cleaned_data["observed_on"]
        if value > timezone.localdate():
            raise forms.ValidationError("参考价观察日期不能晚于今天。")
        return value


class EventForm(SubmissionForm):
    title = forms.CharField(label="事件标题", max_length=200)
    event_date = forms.DateField(
        label="预计发生日期", widget=forms.DateInput(attrs={"type": "date"})
    )
    source = forms.URLField(
        assume_scheme="https", label="来源链接（选填）", required=False, max_length=500
    )
    notes = forms.CharField(
        label="依据与影响说明",
        required=False,
        max_length=5000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    confirmed = forms.BooleanField(label="已核对事件与该商品有关，加入风险提醒", required=False)


class SourceForm(SubmissionForm):
    name = forms.CharField(label="信息源名称", max_length=100)
    url = forms.URLField(
        assume_scheme="https", label="公开 RSS / Atom 地址（HTTPS）", max_length=500
    )
    enabled = forms.BooleanField(label="启用每日读取", required=False)


@login_required
@require_GET
def reports(request):
    orders = active_orders()
    channel = request.GET.get("channel", "")
    if channel:
        orders = orders.filter(channel__code=channel)
    start, end = request.GET.get("start", ""), request.GET.get("end", "")
    try:
        if start:
            orders = orders.filter(created_at__date__gte=date.fromisoformat(start))
        if end:
            orders = orders.filter(created_at__date__lte=date.fromisoformat(end))
    except ValueError:
        start = end = ""
    return render(
        request,
        "insights/reports.html",
        {
            "totals": order_totals(orders),
            "channels": SalesChannel.objects.all(),
            "selected_channel": channel,
            "start": start,
            "end": end,
            "orders": Paginator(orders.select_related("channel"), 30).get_page(
                request.GET.get("page")
            ),
            "channel_totals": [
                (c.name, order_totals(orders.filter(channel=c))) for c in SalesChannel.objects.all()
            ],
        },
    )


@login_required
@require_GET
def risk_list(request):
    page = Paginator(
        SKU.objects.filter(is_active=True)
        .select_related("product", "balance")
        .order_by("created_at", "id"),
        20,
    ).get_page(request.GET.get("page"))
    return render(
        request,
        "insights/risks.html",
        {
            "metrics": [product_metrics(sku) for sku in page],
            "page": page,
            "today": timezone.localdate(),
            "sources": IntelSource.objects.all(),
            "articles": IntelArticle.objects.select_related("source", "matched_sku__product")[:30],
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def market_record(request, sku_id, kind):
    sku = get_object_or_404(SKU, pk=sku_id)
    form_class = PriceForm if kind == "price" else EventForm
    form = form_class(request.POST if request.method == "POST" else None)

    def save(data):
        require_operator(request.user)
        key = data.pop("submission_key")
        if kind == "price":
            data["price_fen"] = to_fen(data.pop("price"))

        def action():
            model = MarketPrice if kind == "price" else MarketEvent
            record = model(sku=sku, **data)
            record.full_clean()
            record.save()
            record_event(request.user, "market." + kind, record)
            return {}

        return execute_once(
            f"market.{kind}:{request.user.pk}",
            key,
            {key: str(value) for key, value in {"sku_id": sku.pk, **data}.items()},
            action,
        )

    return business_form(
        request,
        form=form,
        title="记录参考价格" if kind == "price" else "记录市场事件",
        intro="保留来源与日期，过期价格会标记。事件核对后才进入未来 30 天提醒，不自动改价或下架。",
        save=save,
        destination=lambda result: reverse("risk-list"),
        back_url=reverse("risk-list"),
    )


@login_required
@require_http_methods(["GET", "POST"])
def source_new(request, source_id=None):
    require_admin(request.user)
    source = get_object_or_404(IntelSource, pk=source_id) if source_id else None
    form = SourceForm(
        request.POST if request.method == "POST" else None,
        initial={"name": source.name, "url": source.url, "enabled": source.enabled}
        if source
        else {},
    )

    def save(data):
        key = data.pop("submission_key")

        def action():
            record = (
                IntelSource.objects.select_for_update().get(pk=source_id)
                if source_id
                else IntelSource()
            )
            for field, value in data.items():
                setattr(record, field, value)
            record.full_clean()
            record.save()
            record_event(request.user, "intel.source_saved", record)
            return {}

        return execute_once(
            f"intel.source:{request.user.pk}",
            key,
            dict(source_id=str(source_id or ""), **data),
            action,
        )

    return business_form(
        request,
        form=form,
        title="新增公开信息源",
        intro="仅连接你启用的公开 RSS / Atom 源。未配置时仍可手工记录参考价与事件。",
        save=save,
        destination=lambda result: reverse("risk-list"),
        back_url=reverse("risk-list"),
    )
