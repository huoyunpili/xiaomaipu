from django import forms


class ReferenceForm(forms.Form):
    reference_days = forms.IntegerField(
        label="发货后参考回款天数",
        min_value=1,
        max_value=365,
        help_text="默认 10 天，每天按 24 小时计算。统一应用于待完成订单；退款中暂停参考。",
    )


class RetentionForm(forms.Form):
    image_retention_days = forms.IntegerField(
        label="导出图片保留天数",
        min_value=1,
        max_value=36500,
        required=False,
        help_text="留空永久保留；设置天数后每天自动清理过期图片，历史批次和快照继续保留。",
    )


class ProductForm(forms.Form):
    condition = forms.CharField(label="成色", max_length=100, required=False)
    supplier_wechat = forms.CharField(label="供应商微信备注名称", max_length=100, required=False)
    shipping_note = forms.CharField(label="默认发货备注", max_length=1000, required=False)
    cost = forms.DecimalField(
        label="单件成本（元）", min_value=0, max_value=100000000, decimal_places=2, required=False
    )
    supplier = forms.CharField(label="默认供应商", max_length=100, required=False)


class TradeForm(forms.Form):
    supplier_wechat = forms.CharField(label="本单供应商微信备注", max_length=100, required=False)
    supplier = forms.CharField(
        label="本单供应商",
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={"list": "saved-suppliers", "autocomplete": "off"}),
        help_text="可选择已保存供应商，也可输入新名称。",
    )
    spec = forms.CharField(label="型号规格", max_length=200, required=False)
    receiver = forms.CharField(label="收件人", max_length=100, required=False)
    phone = forms.CharField(label="电话", max_length=100, required=False)
    address = forms.CharField(label="完整地址", max_length=1000, required=False)
    note = forms.CharField(label="本单补充发货备注", max_length=1000, required=False)
    loss = forms.DecimalField(
        label="实际退货运费损失（选填，元）",
        min_value=0,
        max_value=100000000,
        decimal_places=2,
        required=False,
    )
    refund_note = forms.CharField(label="售后备注", max_length=1000, required=False)
    refund_waybill = forms.CharField(
        label="退货单号（API 缺失时补充）", max_length=100, required=False
    )
    recovered = forms.BooleanField(
        label="确认供应商货款已收回",
        required=False,
        help_text="勾选并保存表示已追回；未勾选表示未追回。客户退款尚未完成时也可登记，以实际收到供应商货款为准。",
    )
    correction = forms.DecimalField(
        label="更正本单单件成本（选填，元）",
        min_value=0,
        max_value=100000000,
        decimal_places=2,
        required=False,
    )
    reason = forms.CharField(label="成本更正原因", max_length=300, required=False)

    def clean(self):
        data = super().clean() or {}
        if data.get("correction") is not None and not data.get("reason"):
            self.add_error("reason", "更正历史成本必须填写原因。")
        return data
