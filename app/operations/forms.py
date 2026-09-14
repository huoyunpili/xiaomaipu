from django import forms

from app.common.forms import SubmissionForm
from app.orders.models import OrderItem
from app.shops.models import SalesChannel


class ListingMappingForm(SubmissionForm):
    channel = forms.ModelChoiceField(label="销售渠道", queryset=SalesChannel.objects.all())
    external_listing_id = forms.CharField(label="商品链接 / 商品编号", max_length=200)
    external_spec_id = forms.CharField(label="规格编号（选填）", max_length=200, required=False)
    listing_url = forms.URLField(
        label="商品链接（选填）", max_length=1000, required=False, assume_scheme="https"
    )
    evidence = forms.CharField(label="确认依据", max_length=300)


class FollowUpForm(SubmissionForm):
    snoozed_until = forms.DateTimeField(
        label="稍后提醒时间（选填）",
        required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )
    note = forms.CharField(label="跟进备注（选填）", max_length=300, required=False)


class ThresholdForm(SubmissionForm):
    k1_days = forms.IntegerField(label="K1 供应商待发提醒天数", min_value=0, max_value=3650)
    k3_days = forms.IntegerField(label="K3 长期库存天数", min_value=1, max_value=3650)
    k4_days = forms.IntegerField(label="K4 客户付款后待发提醒天数", min_value=0, max_value=3650)
    k5_days = forms.IntegerField(label="K5 交付后待结算提醒天数", min_value=0, max_value=3650)
    k7_days = forms.IntegerField(label="K7 收货后欠供应商提醒天数", min_value=0, max_value=3650)


class SupplyAllocationForm(SubmissionForm):
    order_item = forms.ModelChoiceField(label="销售订单商品", queryset=OrderItem.objects.none())
    quantity = forms.IntegerField(label="计划供给数量", min_value=1, max_value=1000000)

    def __init__(self, *args, sku_id, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields["order_item"]
        assert isinstance(field, forms.ModelChoiceField)
        field.queryset = OrderItem.objects.filter(
            sku_id=sku_id, order__status__in=["CONFIRMED", "PARTIAL"]
        ).select_related("order")
