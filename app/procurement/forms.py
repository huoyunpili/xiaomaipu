from django import forms

from app.catalog.forms import ConditionForm
from app.catalog.models import SKU
from app.common.forms import SubmissionForm, money_field

from .models import Supplier


class SupplierForm(SubmissionForm):
    name = forms.CharField(label="供应商名称", max_length=100)
    contact = forms.CharField(label="联系方式（选填）", max_length=200, required=False)
    notes = forms.CharField(
        label="备注（选填）",
        max_length=5000,
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    version = forms.IntegerField(widget=forms.HiddenInput, required=False)


class PurchaseAllocationForm(SubmissionForm):
    version = forms.IntegerField(widget=forms.HiddenInput)
    lot_version = forms.IntegerField(widget=forms.HiddenInput)
    quantity = forms.IntegerField(label="本次为订单备货数量", min_value=1, max_value=1000000)
    acknowledged = forms.BooleanField(label="已核对实际货况，确认这组货可用于此订单")


class QuoteForm(ConditionForm):
    sku = forms.ModelChoiceField(
        label="商品", queryset=SKU.objects.filter(is_active=True).select_related("product")
    )
    unit_cost = money_field("单件进货成本（元）")
    field_order = ["sku", "condition_description", "condition_label", "unit_cost"]


class PurchaseForm(QuoteForm):
    direct = forms.BooleanField(
        label="供应商直接发给买家",
        required=False,
        help_text="仅关联销售订单时可用，直发不会增加自有库存。",
    )
    supplier = forms.ModelChoiceField(label="供应商", queryset=Supplier.objects.all())
    quantity = forms.IntegerField(label="采购数量", min_value=1, max_value=1000000, initial=1)
    field_order = [
        "sku",
        "supplier",
        "condition_description",
        "condition_label",
        "quantity",
        "unit_cost",
    ]


class PurchaseActionForm(SubmissionForm):
    version = forms.IntegerField(widget=forms.HiddenInput)
    reason = forms.CharField(label="操作说明", max_length=300)

    def __init__(self, *args, operation, **kwargs):
        super().__init__(*args, **kwargs)
        if operation in {"ship", "return"}:
            self.fields["quantity"] = forms.IntegerField(
                label="本次退回数量" if operation == "return" else "本次发货数量",
                min_value=1,
                max_value=1000000,
            )
        if operation in {"pay", "refund"}:
            self.fields["amount"] = money_field("本次实际金额（元）")


class PurchaseReceiptForm(ConditionForm):
    version = forms.IntegerField(widget=forms.HiddenInput)
    quantity = forms.IntegerField(label="本次实际收货数量", min_value=1, max_value=1000000)
    reason = forms.CharField(label="收货说明", max_length=300, initial="已核对实物并入库")
    field_order = ["condition_description", "condition_label", "quantity", "reason"]


class DirectDispatchForm(ConditionForm):
    version = forms.IntegerField(widget=forms.HiddenInput)
    order_version = forms.IntegerField(widget=forms.HiddenInput)
    quantity = forms.IntegerField(label="本次直发数量", min_value=1, max_value=1000000)
    carrier = forms.CharField(label="物流公司", max_length=100)
    tracking_no = forms.CharField(label="运单号", max_length=100)
    fee = money_field("本次直发费用（元）", initial=0)
    evidence_note = forms.CharField(label="取证说明或未留证原因", max_length=300)
    acknowledged = forms.BooleanField(label="已核对供应商实际发出的货况与买家约定")
