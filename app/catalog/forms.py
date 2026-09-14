from django import forms

from app.common.forms import SubmissionForm, money_field


class ConditionForm(SubmissionForm):
    optional_details = {
        "condition_tags",
        "accessories_description",
        "defect_description",
        "function_description",
        "internal_notes",
        "specification",
        "supplier_name",
        "explicit_selection",
    }

    @property
    def primary_fields(self):
        return [field for field in self.visible_fields() if field.name not in self.optional_details]

    @property
    def extra_fields(self):
        return [field for field in self.visible_fields() if field.name in self.optional_details]

    @property
    def extra_errors(self):
        return any(field.errors for field in self.extra_fields)

    condition_description = forms.CharField(
        label="货况说明",
        required=False,
        max_length=5000,
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "placeholder": "例如：99 新，只有主机，右上角轻微翘边，黑底有漏光，日常使用正常。",
            }
        ),
    )
    condition_label = forms.CharField(
        label="成色（可自己写）",
        required=False,
        max_length=100,
        widget=forms.TextInput(attrs={"placeholder": "99 新 / 轻度使用 / 未检测……"}),
    )
    condition_tags = forms.CharField(
        label="标签（可自己写）",
        required=False,
        max_length=300,
        help_text="例如：无配件、翘边、漏光。标签不会替换货况说明。",
    )
    accessories_description = forms.CharField(label="配件说明", required=False, max_length=5000)
    defect_description = forms.CharField(label="瑕疵说明", required=False, max_length=5000)
    function_description = forms.CharField(label="功能检测", required=False, max_length=5000)
    internal_notes = forms.CharField(
        label="内部备注",
        required=False,
        max_length=5000,
        help_text="仅内部查看，不带入订单的对外货况。",
    )


class ProductForm(ConditionForm):
    name = forms.CharField(label="商品名称", max_length=200)
    specification = forms.CharField(label="规格（选填）", required=False, max_length=200)
    version = forms.IntegerField(widget=forms.HiddenInput, required=False)
    field_order = [
        "name",
        "condition_description",
        "condition_label",
        "specification",
        "condition_tags",
        "accessories_description",
        "defect_description",
        "function_description",
        "internal_notes",
    ]


class NewProductForm(ProductForm):
    initial_quantity = forms.IntegerField(
        label="库存数量",
        min_value=0,
        max_value=1000000,
        required=False,
        initial=0,
        help_text="这段货况对应多少件货。暂时没货填 0；不同货况分别录入。",
    )
    unit_cost = money_field("单件进货成本（元）", required=False)
    field_order = [
        "name",
        "condition_description",
        "condition_label",
        "initial_quantity",
        "unit_cost",
        *ProductForm.field_order[3:],
    ]

    def clean(self):
        data = super().clean() or {}
        data["initial_quantity"] = data.get("initial_quantity") or 0
        if data["initial_quantity"] > 0 and data.get("unit_cost") is None:
            self.add_error("unit_cost", "有库存时请填写单件进货成本；确实无成本可填 0。")
        return data
