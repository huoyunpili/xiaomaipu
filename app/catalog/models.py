import uuid

from django.db import models

from app.common.models import BaseModel


def sku_code():
    return "SK" + uuid.uuid4().hex[:16].upper()


class ConditionFields(models.Model):
    condition_description = models.TextField("货况说明", blank=True)
    condition_label = models.CharField("成色", max_length=100, blank=True)
    condition_tags = models.CharField("自定义标签", max_length=300, blank=True)
    accessories_description = models.TextField("配件说明", blank=True)
    defect_description = models.TextField("瑕疵说明", blank=True)
    function_description = models.TextField("功能检测", blank=True)
    internal_notes = models.TextField("内部备注", blank=True)

    class Meta:
        abstract = True

    def condition_snapshot(self):
        return {field: getattr(self, field) for field in PUBLIC_CONDITION_FIELDS}


PUBLIC_CONDITION_FIELDS = (
    "condition_description",
    "condition_label",
    "condition_tags",
    "accessories_description",
    "defect_description",
    "function_description",
)
CONDITION_FIELDS = (*PUBLIC_CONDITION_FIELDS, "internal_notes")


class Product(BaseModel):
    name = models.CharField("商品名称", max_length=200)
    brand = models.CharField("品牌", max_length=100, blank=True)
    category = models.CharField("分类", max_length=100, blank=True)


class SKU(BaseModel, ConditionFields):
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="skus")
    code = models.CharField(max_length=20, default=sku_code, unique=True)
    specification = models.CharField("规格", max_length=200, blank=True)
    is_active = models.BooleanField(default=True)
    requires_explicit_lot_selection = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=1)

    def __str__(self):
        return f"{self.product.name} · {self.specification or self.code}"
