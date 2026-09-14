from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from app.accounts.policies import require_admin
from app.catalog.models import SKU
from app.common.form_views import business_form
from app.procurement.models import Purchase

from .forms import FollowUpForm, ListingMappingForm, SupplyAllocationForm, ThresholdForm
from .models import BottleneckThreshold, FollowUp
from .projections import (
    DEFAULT_THRESHOLDS,
    KIND_LABELS,
    bottlenecks,
    grouped_bottlenecks,
    supplemental_issues,
)
from .services import (
    allocate_supply,
    save_listing_mapping,
    save_thresholds,
    sync_followups,
    update_followup,
)


@login_required
@require_GET
def bottleneck_list(request):
    kind = request.GET.get("kind", "").upper()
    if kind not in KIND_LABELS:
        kind = ""
    all_cards = sync_followups(bottlenecks(), timezone.now())
    cards = [card for card in all_cards if not kind or card["kind"] == kind]
    page = None
    if kind:
        page = Paginator(cards, 30).get_page(request.GET.get("page"))
        groups = grouped_bottlenecks(cards, kinds=[kind])
        groups[0]["cards"] = list(page)
    else:
        groups = grouped_bottlenecks(all_cards, limit=5)
    counts = {key: 0 for key in KIND_LABELS}
    for card in all_cards:
        counts[card["kind"]] += 1
    return render(
        request,
        "operations/bottlenecks.html",
        {
            "cards": page,
            "bottleneck_groups": groups,
            "kind_summaries": [
                {
                    "key": key,
                    "label": label,
                    "count": counts[key],
                    "unavailable": key == "K6",
                }
                for key, label in KIND_LABELS.items()
            ],
            "selected_kind": kind,
            "supplemental": supplemental_issues(),
            "k6_unavailable": True,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def followup_edit(request, followup_id):
    followup = get_object_or_404(FollowUp, pk=followup_id, resolved_at__isnull=True)
    form = FollowUpForm(
        request.POST if request.method == "POST" else None,
        initial={
            "snoozed_until": followup.snoozed_until,
            "note": followup.note,
        },
    )
    return business_form(
        request,
        form=form,
        title=f"跟进 {followup.kind}",
        intro="稍后提醒只改变提醒时间，实际余额和卡点仍保留；卡点只在业务条件消失后自动解决。",
        save=lambda data: update_followup(
            actor=request.user, followup_id=followup.pk, request_id=request.request_id, **data
        ),
        destination=lambda result: reverse("bottleneck-list") + f"?kind={followup.kind}",
        back_url=reverse("bottleneck-list") + f"?kind={followup.kind}",
    )


@login_required
@require_http_methods(["GET", "POST"])
def listing_mapping_new(request, sku_id):
    sku = get_object_or_404(SKU.objects.select_related("product"), pk=sku_id)
    form = ListingMappingForm(request.POST if request.method == "POST" else None)

    def save(data):
        data["channel_id"] = data.pop("channel").pk
        return save_listing_mapping(
            actor=request.user, sku_id=sku.pk, request_id=request.request_id, **data
        )

    return business_form(
        request,
        form=form,
        title="确认商品链接对应 SKU",
        intro=f"当前 SKU：{sku}。同一个 SKU 可以对应多个渠道链接并共享库存；实际出库仍选择具体实物组。",
        save=save,
        destination=lambda result: reverse("product-detail", args=[sku.pk]),
        back_url=reverse("product-detail", args=[sku.pk]),
    )


@login_required
@require_http_methods(["GET", "POST"])
def supply_allocate(request, purchase_id):
    purchase = get_object_or_404(Purchase.objects.select_related("sku__product"), pk=purchase_id)
    form = SupplyAllocationForm(
        request.POST if request.method == "POST" else None, sku_id=purchase.sku_id
    )

    def save(data):
        data["order_item_id"] = data.pop("order_item").pk
        return allocate_supply(
            actor=request.user, purchase_id=purchase.pk, request_id=request.request_id, **data
        )

    return business_form(
        request,
        form=form,
        title="安排采购供给销售订单",
        intro="一笔采购可安排给多个同 SKU 销售订单；总量不能超过采购有效数量或订单缺口。这里只安排来源，验收入库后仍需核对实物货况再锁定库存。",
        save=save,
        destination=lambda result: reverse("purchase-detail", args=[purchase.pk]),
        back_url=reverse("purchase-detail", args=[purchase.pk]),
    )


@login_required
@require_http_methods(["GET", "POST"])
def threshold_settings(request):
    require_admin(request.user)
    current = DEFAULT_THRESHOLDS.copy()
    current.update(dict(BottleneckThreshold.objects.values_list("kind", "days")))
    initial = {kind.lower() + "_days": days for kind, days in current.items() if kind != "K2"}
    form = ThresholdForm(request.POST if request.method == "POST" else None, initial=initial)
    return business_form(
        request,
        form=form,
        title="卡点建议阈值",
        intro="阈值决定提醒紧迫度和长期库存范围，不改变真实钱货余额。K2 只按每批明确填写的预计到货时间判断。",
        save=lambda data: save_thresholds(
            actor=request.user, request_id=request.request_id, **data
        ),
        destination=lambda result: reverse("bottleneck-list"),
        back_url=reverse("bottleneck-list"),
    )
