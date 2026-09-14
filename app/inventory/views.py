from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from app.accounts.policies import require_admin
from app.catalog.models import CONDITION_FIELDS, SKU
from app.common.form_views import business_form
from app.common.forms import to_fen

from .forms import AdjustmentForm, LotEditForm, ReceiptForm
from .models import StockLot
from .services import adjust_stock, edit_lot, receive_stock


@login_required
@require_http_methods(["GET", "POST"])
def stock_receive(request, sku_id):
    sku = get_object_or_404(SKU.objects.select_related("product"), pk=sku_id)
    form = ReceiptForm(
        request.POST if request.method == "POST" else None,
        initial={field: getattr(sku, field) for field in CONDITION_FIELDS},
    )

    def save(data):
        data["unit_cost_fen"] = to_fen(data.pop("unit_cost"))
        data["unit_freight_fen"] = 0
        return receive_stock(
            actor=request.user, sku_id=sku.pk, request_id=request.request_id, **data
        )

    return business_form(
        request,
        form=form,
        title=f"入库 · {sku.product.name}",
        intro="货况相同的一组可以按数量入库；不同货况分别新增。以下预填的是商品默认描述，可以改成这件货的实际情况。",
        save=save,
        destination=lambda result: reverse("product-detail", args=[sku.pk]),
        button="确认入库",
        back_url=reverse("product-detail", args=[sku.pk]),
    )


@login_required
@require_http_methods(["GET", "POST"])
def lot_edit(request, lot_id):
    lot = get_object_or_404(StockLot, pk=lot_id)
    form = LotEditForm(
        request.POST if request.method == "POST" else None,
        initial={field: getattr(lot, field) for field in (*CONDITION_FIELDS, "label", "version")},
    )
    return business_form(
        request,
        form=form,
        title="修改这件/这组货的说明",
        intro="只更新当前货物描述，保留历史订单货况。被订单锁定的货物需先处理订单后再改。",
        save=lambda data: edit_lot(
            actor=request.user, lot_id=lot.pk, request_id=request.request_id, **data
        ),
        destination=lambda result: reverse("product-detail", args=[lot.sku_id]),
        back_url=reverse("product-detail", args=[lot.sku_id]),
    )


@login_required
@require_http_methods(["GET", "POST"])
def stock_adjust(request, lot_id):
    require_admin(request.user)
    lot = get_object_or_404(StockLot, pk=lot_id)
    form = AdjustmentForm(
        request.POST if request.method == "POST" else None,
        initial={"actual_qty": lot.on_hand_qty, "version": lot.version},
    )
    return business_form(
        request,
        form=form,
        title="盘点这件/这组货",
        intro=f"当前账面 {lot.on_hand_qty} 件，其中已锁定 {lot.reserved_qty} 件。确认后按差额调整库存，并保留原因和流水。成本沿用这组货的入库成本。",
        save=lambda data: adjust_stock(
            actor=request.user, lot_id=lot.pk, request_id=request.request_id, **data
        ),
        destination=lambda result: reverse("product-detail", args=[lot.sku_id]),
        button="确认盘点差额",
        back_url=reverse("product-detail", args=[lot.sku_id]),
    )
