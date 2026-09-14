from django import forms

from app.catalog.forms import ConditionForm
from app.common.forms import SubmissionForm, money_field


class ReceiptForm(ConditionForm):
    label = forms.CharField(label="这件/这组货的名称（选填）", max_length=100, required=False)
    quantity = forms.IntegerField(label="入库数量", min_value=1, max_value=1000000, initial=1)
    unit_cost = money_field("单件进货成本（元）")
    supplier_name = forms.CharField(label="来源/供应商（选填）", max_length=100, required=False)
    reason = forms.CharField(label="入库原因", max_length=300, initial="采购/期初入库")
    explicit_selection = forms.BooleanField(
        label="这件/这组货需要单独选货",
        required=False,
        help_text="不同货况会自动启用；开启后，订单必须选到实际出售的货。",
    )
    field_order = [
        "label",
        "condition_description",
        "condition_label",
        "quantity",
        "unit_cost",
        "condition_tags",
        "accessories_description",
        "defect_description",
        "function_description",
        "internal_notes",
        "supplier_name",
        "reason",
        "explicit_selection",
    ]


class LotEditForm(ConditionForm):
    label = forms.CharField(label="这件/这组货的名称（选填）", max_length=100, required=False)
    version = forms.IntegerField(widget=forms.HiddenInput)


class AdjustmentForm(SubmissionForm):
    actual_qty = forms.IntegerField(label="这组货实际数到的数量", min_value=0, max_value=1000000)
    version = forms.IntegerField(widget=forms.HiddenInput)
    reason = forms.CharField(label="盘点原因", max_length=300)
