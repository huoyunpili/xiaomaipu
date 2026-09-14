from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from app.common.form_views import business_form
from app.common.forms import to_fen
from app.inventory.models import StockMovement

from .forms import NewProductForm, ProductForm
from .models import CONDITION_FIELDS, SKU
from .services import create_product_with_stock, save_product


@login_required
@require_GET
def product_list(request, for_order=False):
    query = request.GET.get("q", "").strip()[:100]
    skus = SKU.objects.select_related("product", "balance").filter(is_active=True)
    if query:
        skus = skus.filter(
            Q(product__name__icontains=query)
            | Q(condition_description__icontains=query)
            | Q(condition_label__icontains=query)
            | Q(lots__condition_description__icontains=query)
            | Q(lots__condition_label__icontains=query)
            | Q(condition_tags__icontains=query)
        ).distinct()
    page = Paginator(skus.order_by("-created_at"), 30).get_page(request.GET.get("page"))
    return render(
        request,
        "orders/choose.html" if for_order else "catalog/list.html",
        {"page": page, "query": query, "for_order": for_order},
    )


@login_required
@require_GET
def product_detail(request, sku_id):
    sku = get_object_or_404(SKU.objects.select_related("product", "balance"), pk=sku_id)
    return render(
        request,
        "catalog/detail.html",
        {
            "sku": sku,
            "lots": sku.lots.all(),
            "movements": StockMovement.objects.filter(sku=sku).select_related("lot")[:50],
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def product_form(request, sku_id=None):
    sku = get_object_or_404(SKU.objects.select_related("product"), pk=sku_id) if sku_id else None
    initial = (
        {field: getattr(sku, field) for field in (*CONDITION_FIELDS, "specification", "version")}
        if sku
        else {}
    )
    if sku:
        initial["name"] = sku.product.name
    form_class = ProductForm if sku else NewProductForm
    form = form_class(request.POST if request.method == "POST" else None, initial=initial)

    def save(data):
        if sku:
            return save_product(
                actor=request.user, sku_id=sku_id, request_id=request.request_id, **data
            )
        data.pop("version", None)
        cost = data.pop("unit_cost")
        data["unit_cost_fen"] = to_fen(cost) if cost is not None else None
        data["unit_freight_fen"] = 0
        return create_product_with_stock(actor=request.user, request_id=request.request_id, **data)

    return business_form(
        request,
        form=form,
        title="修改商品" if sku else "新增商品",
        intro=(
            "修改商品默认描述不会改动已入库实物；实物数量请在商品详情的库存区调整。"
            if sku
            else "把这批货的名称、货况和数量一起记下来，保存后直接进入库存。同款但货况不同的货，可以继续添加另一组。"
        ),
        save=save,
        button="保存修改" if sku else "保存商品和库存",
        destination=lambda result: reverse("product-detail", args=[result["sku_id"]]),
        back_url=reverse("product-list"),
    )
