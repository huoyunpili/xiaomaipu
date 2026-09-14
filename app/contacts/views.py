from django import forms
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from app.accounts.policies import require_operator
from app.common.business import BusinessError, record_event
from app.common.form_views import business_form
from app.common.forms import SubmissionForm
from app.common.services import execute_once
from app.orders.models import SalesOrder

from .models import Customer


class CustomerForm(SubmissionForm):
    name = forms.CharField(label="客户称呼", max_length=100)
    contact = forms.CharField(label="联系方式或渠道账号（选填）", max_length=200, required=False)
    tags = forms.CharField(label="标签（自己写）", max_length=300, required=False)
    notes = forms.CharField(
        label="客户备注", required=False, max_length=5000, widget=forms.Textarea(attrs={"rows": 3})
    )
    risk_reason = forms.CharField(
        label="风险原因或事实记录",
        required=False,
        max_length=5000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    suggestion = forms.ChoiceField(
        label="后续处理提醒", choices=Customer._meta.get_field("suggestion").choices or []
    )
    version = forms.IntegerField(required=False, widget=forms.HiddenInput)


def save_customer(*, actor, submission_key, customer_id=None, version=None, **data):
    require_operator(actor)

    def action():
        customer = (
            Customer.objects.select_for_update().get(pk=customer_id) if customer_id else Customer()
        )
        if customer_id and customer.version != version:
            raise BusinessError("客户档案已变化，请刷新后保存。")
        for key in ("name", "contact", "tags", "notes", "risk_reason", "suggestion"):
            setattr(customer, key, data.get(key, "NONE" if key == "suggestion" else ""))
        if customer.suggestion == "PENDING" and not customer.risk_reason.strip():
            raise BusinessError("设置风险提醒时请记录具体原因。")
        customer.version += 1
        customer.full_clean()
        customer.save()
        record_event(actor, "customer.saved", customer)
        return {"customer_id": str(customer.pk)}

    return execute_once(
        f"customer.save:{actor.pk}",
        submission_key,
        dict(customer_id=str(customer_id or ""), version=version, **data),
        action,
    )


@login_required
@require_GET
def customer_list(request):
    query = request.GET.get("q", "").strip()[:100]
    customers = Customer.objects.all()
    if query:
        customers = customers.filter(
            Q(name__icontains=query) | Q(contact__icontains=query) | Q(tags__icontains=query)
        )
    return render(
        request,
        "contacts/list.html",
        {"customers": Paginator(customers, 30).get_page(request.GET.get("page")), "q": query},
    )


@login_required
@require_http_methods(["GET", "POST"])
def customer_edit(request, customer_id=None):
    customer = get_object_or_404(Customer, pk=customer_id) if customer_id else None
    fields = ("name", "contact", "tags", "notes", "risk_reason", "suggestion", "version")
    form = CustomerForm(
        request.POST if request.method == "POST" else None,
        initial={key: getattr(customer, key) for key in fields}
        if customer
        else {"suggestion": "NONE"},
    )
    return business_form(
        request,
        form=form,
        title="客户档案",
        intro="记录实际交易中有用的信息。风险只提醒，由你决定是否处理，不会自动拉黑或取消订单。",
        save=lambda data: save_customer(actor=request.user, customer_id=customer_id, **data),
        destination=lambda result: reverse("customer-detail", args=[result["customer_id"]]),
        back_url=reverse("customer-list"),
    )


@login_required
@require_GET
def customer_detail(request, customer_id):
    customer = get_object_or_404(Customer, pk=customer_id)
    return render(
        request,
        "contacts/detail.html",
        {
            "customer": customer,
            "orders": Paginator(
                SalesOrder.objects.filter(customer=customer).select_related("channel"), 30
            ).get_page(request.GET.get("page")),
        },
    )
