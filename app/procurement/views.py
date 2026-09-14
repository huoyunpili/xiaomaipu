from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from app.catalog.models import CONDITION_FIELDS
from app.common.form_views import business_form
from app.common.forms import to_fen
from app.orders.models import OrderItem

from .allocation import reserve_purchase_receipt
from .direct import dispatch_direct
from .forms import (
    DirectDispatchForm,
    PurchaseActionForm,
    PurchaseAllocationForm,
    PurchaseForm,
    PurchaseReceiptForm,
    QuoteForm,
    SupplierForm,
)
from .models import Purchase, PurchaseReceipt, Supplier, SupplierQuote
from .services import add_quote, create_purchase, purchase_action, save_supplier


@login_required
@require_GET
def purchase_list(request):
    search = request.GET.get("q", "").strip()
    purchases = Purchase.objects.all()
    if search:
        purchases = purchases.filter(
            Q(number__icontains=search)
            | Q(product_name__icontains=search)
            | Q(supplier_name__icontains=search)
        )
    return render(
        request,
        "procurement/list.html",
        {"page_obj": Paginator(purchases, 30).get_page(request.GET.get("page")), "q": search},
    )


@login_required
@require_GET
def supplier_list(request):
    return render(
        request,
        "procurement/suppliers.html",
        {"suppliers": Paginator(Supplier.objects.all(), 30).get_page(request.GET.get("page"))},
    )


@login_required
@require_http_methods(["GET", "POST"])
def supplier_form(request, supplier_id=None):
    supplier = get_object_or_404(Supplier, pk=supplier_id) if supplier_id else None
    initial = (
        {key: getattr(supplier, key) for key in ("name", "contact", "notes", "version")}
        if supplier
        else {}
    )
    form = SupplierForm(request.POST if request.method == "POST" else None, initial=initial)
    return business_form(
        request,
        form=form,
        title="修改供应商" if supplier else "新增供应商",
        intro="记下平时拿货的人或商家，联系方式和备注可不填。",
        save=lambda data: save_supplier(
            actor=request.user, supplier_id=supplier_id, request_id=request.request_id, **data
        ),
        destination=lambda result: reverse("supplier-detail", args=[result["supplier_id"]]),
        back_url=reverse("supplier-list"),
    )


@login_required
@require_GET
def supplier_detail(request, supplier_id):
    supplier = get_object_or_404(Supplier, pk=supplier_id)
    quotes = supplier.quotes.select_related("sku__product")
    return render(
        request,
        "procurement/supplier.html",
        {"supplier": supplier, "quotes": Paginator(quotes, 30).get_page(request.GET.get("page"))},
    )


@login_required
@require_http_methods(["GET", "POST"])
def quote_new(request, supplier_id):
    supplier = get_object_or_404(Supplier, pk=supplier_id)
    form = QuoteForm(request.POST if request.method == "POST" else None)

    def save(data):
        data["sku_id"] = data.pop("sku").pk
        data["unit_cost_fen"] = to_fen(data.pop("unit_cost"))
        return add_quote(
            actor=request.user, supplier_id=supplier.pk, request_id=request.request_id, **data
        )

    return business_form(
        request,
        form=form,
        title=f"记录报价 · {supplier.name}",
        intro="价格或货况变化时新增一条报价，保留之前的记录。",
        save=save,
        destination=lambda result: reverse("supplier-detail", args=[supplier.pk]),
        back_url=reverse("supplier-detail", args=[supplier.pk]),
    )


@login_required
@require_http_methods(["GET", "POST"])
def purchase_new(request, quote_id=None, order_item_id=None):
    quote = get_object_or_404(SupplierQuote, pk=quote_id) if quote_id else None
    initial = (
        {
            "sku": quote.sku_id,
            "supplier": quote.supplier_id,
            "unit_cost": Decimal(quote.unit_cost_fen) / 100,
            **{field: getattr(quote, field) for field in CONDITION_FIELDS},
        }
        if quote
        else {}
    )
    item = (
        get_object_or_404(OrderItem.objects.select_related("order"), pk=order_item_id)
        if order_item_id
        else None
    )
    if item:
        pending = sum(p.planned_qty for p in Purchase.objects.filter(order_item=item))
        initial.update(
            sku=item.sku_id, quantity=max(item.shortage_qty - pending, 0), **item.condition_snapshot
        )
    form = PurchaseForm(request.POST if request.method == "POST" else None, initial=initial)
    if item:
        form.fields["sku"].disabled = True
    if quote:
        form.fields["sku"].disabled = True
        form.fields["supplier"].disabled = True

    def save(data):
        data["sku_id"] = data.pop("sku").pk
        data["supplier_id"] = data.pop("supplier").pk
        data["unit_cost_fen"] = to_fen(data.pop("unit_cost"))
        return create_purchase(
            actor=request.user,
            quote_id=quote.pk if quote else None,
            order_item_id=item.pk if item else None,
            request_id=request.request_id,
            **data,
        )

    return business_form(
        request,
        form=form,
        title="为缺货订单采购" if item else "新增采购",
        intro=(
            f"订单 {item.order.number} · {item.order.customer_name} · 当前缺 {item.shortage_qty} 件，已安排采购或到货待备 {pending} 件。到货后核对实际货况，再为订单备货。"
            if item
            else "先记录要进的货，确认收货后才增加库存。不同货况可以分别采购，到货时也可按实际情况分组录入。"
        ),
        save=save,
        destination=lambda result: reverse("purchase-detail", args=[result["purchase_id"]]),
        back_url=reverse("purchase-list"),
        button="保存采购单",
    )


@login_required
@require_GET
def purchase_detail(request, purchase_id):
    purchase = get_object_or_404(
        Purchase.objects.select_related("order_item__order"), pk=purchase_id
    )
    return render(
        request,
        "procurement/detail.html",
        {
            "purchase": purchase,
            "receipts": purchase.receipts.select_related("lot"),
            "events": purchase.events.all()[:100],
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def purchase_allocate(request, receipt_id):
    receipt = get_object_or_404(
        PurchaseReceipt.objects.select_related("lot", "purchase__order_item__order"), pk=receipt_id
    )
    item = receipt.purchase.order_item
    if item is None:
        raise Http404
    form = PurchaseAllocationForm(
        request.POST if request.method == "POST" else None,
        initial={
            "version": item.order.version,
            "lot_version": receipt.lot.version,
            "quantity": min(item.shortage_qty, receipt.lot.available_qty),
        },
    )
    return business_form(
        request,
        form=form,
        title="核对到货并为订单备货",
        intro=f"{item.order.customer_name} · {item.order.number} · 当前缺 {item.shortage_qty} 件。",
        save=lambda data: reserve_purchase_receipt(
            actor=request.user, receipt_id=receipt.pk, request_id=request.request_id, **data
        ),
        destination=lambda result: reverse("order-detail", args=[result["order_id"]]),
        back_url=reverse("purchase-detail", args=[receipt.purchase_id]),
        template_name="procurement/allocate.html",
        extra_context={"item": item, "lot": receipt.lot},
        button="确认货况并锁定库存",
    )


@login_required
@require_http_methods(["GET", "POST"])
def direct_dispatch(request, purchase_id):
    purchase = get_object_or_404(
        Purchase.objects.select_related("order_item__order"), pk=purchase_id, direct=True
    )
    if not purchase.order_item:
        raise Http404
    form = DirectDispatchForm(
        request.POST if request.method == "POST" else None,
        initial={
            "version": purchase.version,
            "order_version": purchase.order_item.order.version,
            "quantity": min(purchase.pending_qty, purchase.order_item.shortage_qty),
            **{field: getattr(purchase, field) for field in CONDITION_FIELDS},
        },
    )

    def save(data):
        data["fee_fen"] = to_fen(data.pop("fee"))
        return dispatch_direct(
            actor=request.user, purchase_id=purchase.pk, request_id=request.request_id, **data
        )

    return business_form(
        request,
        form=form,
        title="确认供应商已直发",
        intro="确认实际发出的货况与物流，成本沿用采购单。这次发货直接关联销售订单，不增加或扣减自有库存。",
        save=save,
        destination=lambda result: reverse("order-detail", args=[result["order_id"]]),
        back_url=reverse("purchase-detail", args=[purchase.pk]),
        button="确认直发",
    )


@login_required
@require_http_methods(["GET", "POST"])
def purchase_operate(request, purchase_id, operation, receipt_id=None):
    titles = {
        "return": "登记退回供应商",
        "order": "确认已向供应商下单",
        "ship": "记录供应商发货",
        "receive": "确认收货入库",
        "close": "关闭剩余采购",
        "pay": "登记采购付款",
        "refund": "登记供应商退款",
    }
    if operation not in titles:
        raise Http404
    purchase = get_object_or_404(Purchase, pk=purchase_id)
    receipt = (
        get_object_or_404(
            PurchaseReceipt.objects.select_related("lot"), pk=receipt_id, purchase=purchase
        )
        if operation == "return"
        else None
    )
    initial = {"version": purchase.version}
    form: PurchaseReceiptForm | PurchaseActionForm
    if operation == "receive":
        initial.update({field: getattr(purchase, field) for field in CONDITION_FIELDS})
        form = PurchaseReceiptForm(
            request.POST if request.method == "POST" else None, initial=initial
        )
    else:
        form = PurchaseActionForm(
            request.POST if request.method == "POST" else None, initial=initial, operation=operation
        )

    def save(data):
        if "amount" in data:
            data["amount_fen"] = to_fen(data.pop("amount"))
        return purchase_action(
            actor=request.user,
            purchase_id=purchase.pk,
            receipt_id=receipt.pk if receipt else None,
            operation=operation,
            request_id=request.request_id,
            **data,
        )

    intro = (
        f"{purchase.product_name} · {purchase.supplier_name} · 剩余待收 {purchase.pending_qty} 件。"
    )
    if operation == "receive":
        intro += "只填写本次实际收到且可入库的数量，货况不同请分次录入；单件进货成本沿用采购单。"
    elif operation == "return":
        intro += "退回的货将扣减可售库存和采购应付，原记录保留；供应商实际退款到账后再登记退款。"
    elif operation == "close":
        intro += "确认剩余货物不再收货后关闭；已收到的库存保留，已付款需另行登记实际退款。"
    elif operation in {"pay", "refund"}:
        intro += "只登记实际发生的资金，不会发起转账。"
    return business_form(
        request,
        form=form,
        title=titles[operation],
        intro=intro,
        save=save,
        destination=lambda result: reverse("purchase-detail", args=[purchase.pk]),
        back_url=reverse("purchase-detail", args=[purchase.pk]),
        button=titles[operation],
    )
