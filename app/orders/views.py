from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from app.accounts.policies import require_admin
from app.catalog.models import SKU
from app.common.form_views import business_form
from app.common.forms import to_fen
from app.evidence.models import EvidenceVideo
from app.finance.services import record_money
from app.inventory.models import StockLot

from .forms import (
    CancelRemainingForm,
    DispatchForm,
    InspectionForm,
    MoneyForm,
    OrderActionForm,
    OrderForm,
    RefundForm,
    ReturnForm,
)
from .models import Reservation, ReturnReceipt, SalesOrder, Shipment
from .returns import inspect_return, receive_return
from .services import create_order, order_action


@login_required
@require_GET
def order_list(request):
    query = request.GET.get("q", "").strip()[:100]
    status = request.GET.get("status", "")
    orders = SalesOrder.objects.select_related("channel").prefetch_related("items")
    if query:
        orders = orders.filter(
            Q(number__icontains=query)
            | Q(customer_name__icontains=query)
            | Q(items__title_snapshot__icontains=query)
        ).distinct()
    if status in SalesOrder.Status.values:
        orders = orders.filter(status=status)
    page = Paginator(orders, 30).get_page(request.GET.get("page"))
    return render(
        request,
        "orders/list.html",
        {
            "page": page,
            "query": query,
            "selected_status": status,
            "statuses": SalesOrder.Status.choices,
        },
    )


@login_required
@require_GET
def order_detail(request, order_id):
    order = get_object_or_404(SalesOrder.objects.select_related("channel"), pk=order_id)
    return render(
        request,
        "orders/detail.html",
        {
            "order": order,
            "videos": EvidenceVideo.objects.filter(order=order),
            "shipments": Shipment.objects.filter(order=order).prefetch_related(
                "reservations__item"
            ),
            "items": order.items.select_related("sku").prefetch_related(
                "reservations__lot", "purchases"
            ),
            "returns": ReturnReceipt.objects.filter(reservation__item__order=order).select_related(
                "reservation"
            ),
            "events": order.events.all()[:50],
            "entries": order.money_entries.all()[:50],
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def order_new(request, sku_id):
    sku = get_object_or_404(
        SKU.objects.select_related("product", "balance"), pk=sku_id, is_active=True
    )
    initial = {}
    if request.method == "GET" and request.GET.get("lot"):
        from django.core.exceptions import ValidationError
        from django.http import Http404

        try:
            lot = get_object_or_404(StockLot, pk=request.GET["lot"], sku=sku)
        except (ValidationError, ValueError) as exc:
            raise Http404 from exc
        initial["selected_lot"] = lot.pk
    form = OrderForm(request.POST if request.method == "POST" else None, sku=sku, initial=initial)

    def save(data):
        data["channel_id"] = data.pop("channel").pk
        customer = data.pop("customer", None)
        data["customer_id"] = customer.pk if customer else None
        lot = data.pop("selected_lot")
        data["selected_lot_id"] = lot.pk if lot else None
        data["unit_price_fen"] = to_fen(data.pop("unit_price"))
        return create_order(
            actor=request.user, sku_id=sku.pk, request_id=request.request_id, **data
        )

    return business_form(
        request,
        form=form,
        title="填写销售订单",
        intro="先选实际卖出的货，再填数量、成交价和买家。保存后可检查订单，确认时才锁库存。",
        save=save,
        destination=lambda result: reverse("order-detail", args=[result["order_id"]]),
        button="保存订单，下一步核对",
        back_url=reverse("order-choose"),
        template_name="orders/new.html",
        extra_context={"sku": sku},
    )


ACTION_TEXT = {
    "cancel_remaining": (
        "关闭剩余未发数量",
        "仅关闭尚未发出的数量，已发货和关联采购保留。核对本次应收减免；实际退款到账后另行登记，避免重复减免。",
    ),
    "confirm": ("确认订单 / 补锁库存", "确认后锁定可用库存，缺货会明确显示，补货后可再次补锁。"),
    "cancel": ("取消订单", "将释放尚未出库的库存，应收归零；已收款保留，需单独登记退款。"),
    "ship": (
        "确认发货 / 交付",
        "将扣减实际库存并保留成本与货况。请填写实际费用；未留发货视频仅提醒，不拦截。",
    ),
    "complete": ("确认履约完成", "仅确认商品已完成交付；收款仍需单独登记实际到账。"),
}


@login_required
@require_http_methods(["GET", "POST"])
def order_operate(request, order_id, operation):
    order = get_object_or_404(SalesOrder.objects.select_related("channel"), pk=order_id)
    if operation not in ACTION_TEXT:
        from django.http import Http404

        raise Http404
    title, intro = ACTION_TEXT[operation]
    initial: dict = {"version": order.version}
    if operation == "cancel_remaining":
        from decimal import Decimal

        remaining_amount = sum(
            (item.quantity - item.shipped_qty - item.cancelled_qty) * item.unit_price_fen
            for item in order.items.all()
        )
        initial["reduction"] = Decimal(min(remaining_amount, order.adjusted_due_fen)) / 100
    if operation == "ship":
        available = sum(item.reserved_qty for item in order.items.all())
        initial["quantity"] = available
        intro += f" 当前已备货 {available} 件。按备货记录的先后顺序发出，可在下方核对本次实物。"
    if order.channel.code == "XIANYU" and operation in ("confirm", "complete"):
        required_reason = "已核对买家付款" if operation == "confirm" else "已核对平台交易成功"
        intro += f" 请核对闲鱼后，在操作说明中输入“{required_reason}”。"
    form_type = DispatchForm if operation == "ship" else OrderActionForm
    if operation == "cancel_remaining":
        form_type = CancelRemainingForm
    form = form_type(request.POST if request.method == "POST" else None, initial=initial)

    def save(data):
        if "fulfillment_fee" in data:
            data["fulfillment_fee_fen"] = to_fen(data.pop("fulfillment_fee"))
        if "reduction" in data:
            data["reduction_fen"] = to_fen(data.pop("reduction"))
        return order_action(
            actor=request.user,
            order_id=order.pk,
            action_name=operation,
            request_id=request.request_id,
            **data,
        )

    return business_form(
        request,
        form=form,
        title=title,
        intro=intro,
        save=save,
        destination=lambda result: reverse("order-detail", args=[order.pk]),
        button=title,
        back_url=reverse("order-detail", args=[order.pk]),
        template_name="orders/dispatch.html" if operation == "ship" else "business_form.html",
        extra_context={
            "allocations": Reservation.objects.filter(item__order=order, status="ACTIVE")
            .select_related("lot")
            .order_by("item__sku_id", "item_id", "created_at", "id")
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def money_record(request, order_id, kind):
    order = get_object_or_404(SalesOrder, pk=order_id)
    titles = {"RECEIPT": "登记实际收款", "REFUND": "登记退款 / 应收减免", "FEE": "补录实际费用"}
    if kind not in titles:
        from django.http import Http404

        raise Http404
    if order.status == SalesOrder.Status.COMPLETED and kind in ("REFUND", "FEE"):
        require_admin(request.user)
    form_type = RefundForm if kind == "REFUND" else MoneyForm
    form = form_type(
        request.POST if request.method == "POST" else None, initial={"version": order.version}
    )

    def save(data):
        data["amount_fen"] = to_fen(data.pop("amount"))
        data["reduction_fen"] = to_fen(data.pop("reduction", 0))
        return record_money(
            actor=request.user, order_id=order.pk, kind=kind, request_id=request.request_id, **data
        )

    intro = "只记录真实发生的收支。闲鱼买家付款是平台托管，只有实际到账才登记收款。"
    if kind == "REFUND":
        intro = "实退金额影响净收款，应收减免影响还该收多少，两者分开填写。退回超收款通常无需减免应收；取消订单已自动减免全部应收。只减免未收款部分时实退可填 0。退款不会自动回补库存。"
    return business_form(
        request,
        form=form,
        title=titles[kind],
        intro=intro,
        save=save,
        destination=lambda result: reverse("order-detail", args=[order.pk]),
        back_url=reverse("order-detail", args=[order.pk]),
    )


@login_required
@require_http_methods(["GET", "POST"])
def return_receive(request, reservation_id):
    reservation = get_object_or_404(Reservation.objects.select_related("item"), pk=reservation_id)
    form = ReturnForm(request.POST if request.method == "POST" else None)
    return business_form(
        request,
        form=form,
        title="登记收到退货",
        intro="只登记实际收到的货物，先进入待检，不会立即增加可售库存。",
        save=lambda data: receive_return(
            actor=request.user, reservation_id=reservation.pk, request_id=request.request_id, **data
        ),
        destination=lambda result: reverse("order-detail", args=[result["order_id"]]),
        back_url=reverse("order-detail", args=[reservation.item.order_id]),
    )


@login_required
@require_http_methods(["GET", "POST"])
def return_inspect(request, return_id):
    require_admin(request.user)
    receipt = get_object_or_404(
        ReturnReceipt.objects.select_related("reservation__item"), pk=return_id
    )
    form = InspectionForm(
        request.POST if request.method == "POST" else None,
        initial=receipt.reservation.condition_snapshot,
    )
    return business_form(
        request,
        form=form,
        title="退货验收",
        intro="核对退回实物的当前货况。可售后独立入库；报废不回补库存。尚需维修时先保留待检。",
        save=lambda data: inspect_return(
            actor=request.user, return_id=receipt.pk, request_id=request.request_id, **data
        ),
        destination=lambda result: reverse("order-detail", args=[result["order_id"]]),
        back_url=reverse("order-detail", args=[receipt.reservation.item.order_id]),
    )
