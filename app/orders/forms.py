from typing import cast

from django import forms
from django.db.models import F

from app.catalog.forms import ConditionForm
from app.common.forms import SubmissionForm, money_field
from app.contacts.models import Customer
from app.inventory.models import StockLot
from app.shops.models import SalesChannel


class ChannelChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return str(obj.name)


class StockChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        description = obj.condition_description or obj.condition_label or "货况未记录"
        for label, field in (
            ("配件", "accessories_description"),
            ("瑕疵", "defect_description"),
            ("功能", "function_description"),
            ("标签", "condition_tags"),
        ):
            if getattr(obj, field):
                description += f"\n{label}：{getattr(obj, field)}"
        return f"{obj.label or '未命名货物'} · 可售 {obj.available_qty} 件\n成色：{obj.condition_label or '未记录'}\n{description}"


class OrderForm(SubmissionForm):
    customer = forms.ModelChoiceField(
        label="关联已有客户（选填）", queryset=Customer.objects.all(), required=False
    )
    customer_name = forms.CharField(label="客户称呼/临时标识", max_length=100)
    channel = ChannelChoiceField(
        label="销售渠道", queryset=SalesChannel.objects.filter(is_active=True)
    )
    selected_lot = StockChoiceField(
        label="实际出售的这件/这组货",
        queryset=StockLot.objects.none(),
        required=False,
        empty_label="同货况库存自动分配（按入库先后）",
        widget=forms.RadioSelect,
    )
    quantity = forms.IntegerField(label="数量", min_value=1, max_value=1000000, initial=1)
    unit_price = money_field("成交单价（元）")
    external_order_no = forms.CharField(label="渠道订单号（选填）", max_length=100, required=False)

    def __init__(self, *args, sku, **kwargs):
        super().__init__(*args, **kwargs)
        lots = StockLot.objects.filter(sku=sku, on_hand_qty__gt=F("reserved_qty"))
        cast(forms.ModelChoiceField, self.fields["selected_lot"]).queryset = lots
        self.fields["selected_lot"].required = sku.requires_explicit_lot_selection
        choice = cast(forms.ModelChoiceField, self.fields["selected_lot"])
        if sku.requires_explicit_lot_selection:
            choice.empty_label = None
        if not self.is_bound and not self.initial.get("selected_lot") and lots.count() == 1:
            self.initial["selected_lot"] = lots.get().pk


class OrderActionForm(SubmissionForm):
    version = forms.IntegerField(widget=forms.HiddenInput)
    reason = forms.CharField(label="操作说明", max_length=300, required=False)


class DispatchForm(OrderActionForm):
    quantity = forms.IntegerField(
        label="本次发货数量",
        min_value=1,
        max_value=1000000,
        help_text="只扣减本次发出的数量，剩余货物可后续继续发货。",
    )
    delivery_method = forms.ChoiceField(
        label="交付方式",
        choices=[("EXPRESS", "快递发货"), ("PICKUP", "客户自提"), ("HANDOVER", "当面交付")],
    )
    carrier = forms.CharField(label="物流公司（快递必填）", max_length=100, required=False)
    tracking_no = forms.CharField(label="运单号（快递必填）", max_length=100, required=False)
    fulfillment_fee = money_field("实际发货运费、包装等费用合计（元）", initial=0)


class CancelRemainingForm(OrderActionForm):
    reduction = money_field("本次减免应收（元）")
    reason = forms.CharField(label="关闭剩余数量的原因", max_length=250)


class MoneyForm(OrderActionForm):
    occurred_at = forms.DateTimeField(
        label="实际收支时间（不清楚可留空）",
        required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )
    account_type = forms.ChoiceField(
        label="实际收支账户",
        choices=[
            ("UNKNOWN", "未记录"),
            ("BANK", "银行卡"),
            ("ALIPAY", "支付宝"),
            ("WECHAT", "微信"),
            ("CASH", "现金"),
        ],
        required=False,
    )

    amount = money_field("实际发生金额（元）")
    reason = forms.CharField(label="到账依据/原因", max_length=300)


class RefundForm(MoneyForm):
    reduction = money_field("本次同时减免的应收（元）", initial=0)


class ReturnForm(SubmissionForm):
    quantity = forms.IntegerField(label="实际收到的退货数量", min_value=1, max_value=1000000)
    reason = forms.CharField(label="退货原因", max_length=300)


class InspectionForm(ConditionForm):
    result = forms.ChoiceField(
        label="验收结果",
        choices=[("RESTOCKED", "确认可售，重新入库"), ("SCRAPPED", "不可售，确认报废")],
    )
    reason = forms.CharField(label="验收说明", max_length=300)
    field_order = [
        "result",
        "reason",
        "condition_description",
        "condition_label",
        "condition_tags",
        "accessories_description",
        "defect_description",
        "function_description",
        "internal_notes",
    ]


class CustomerPaymentForm(OrderActionForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop("reason")

    amount = money_field("客户向平台付款金额（元）")
    source_ref = forms.CharField(label="付款凭据编号", max_length=200)
    evidence = forms.CharField(label="核对依据", max_length=300)
    platform_status = forms.CharField(label="平台付款状态备注", max_length=100, required=False)
    occurred_at = forms.DateTimeField(
        label="实际付款时间（不清楚可留空）",
        required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )
