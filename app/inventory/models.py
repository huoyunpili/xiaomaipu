from django.db import models

from app.catalog.models import ConditionFields
from app.common.models import BaseModel


class InventoryBalance(BaseModel):
    sku = models.OneToOneField("catalog.SKU", on_delete=models.PROTECT, related_name="balance")
    on_hand_qty = models.PositiveIntegerField(default=0)
    reserved_qty = models.PositiveIntegerField(default=0)
    inspection_qty = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(on_hand_qty__gte=models.F("reserved_qty")),
                name="balance_reserve_within_stock",
            )
        ]

    @property
    def available_qty(self):
        return self.on_hand_qty - self.reserved_qty


class StockLot(BaseModel, ConditionFields):
    @property
    def purchase_cost_fen(self):
        return self.unit_cost_fen + self.unit_freight_fen

    sku = models.ForeignKey("catalog.SKU", on_delete=models.PROTECT, related_name="lots")
    label = models.CharField("这件/这组货的名称", max_length=100, blank=True)
    supplier_name = models.CharField("来源/供应商", max_length=100, blank=True)
    unit_cost_fen = models.PositiveBigIntegerField()
    unit_freight_fen = models.PositiveBigIntegerField(default=0)
    received_qty = models.PositiveIntegerField()
    on_hand_qty = models.PositiveIntegerField()
    reserved_qty = models.PositiveIntegerField(default=0)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(on_hand_qty__gte=models.F("reserved_qty")),
                name="lot_reserve_within_stock",
            )
        ]

    @property
    def available_qty(self):
        return self.on_hand_qty - self.reserved_qty

    def __str__(self):
        description = self.condition_description or self.condition_label or "货况未记录"
        return f"{self.label or str(self.id)[:8]} · {description[:45]} · 可售 {self.available_qty}"


class StockMovement(BaseModel):
    sku = models.ForeignKey("catalog.SKU", on_delete=models.PROTECT)
    lot = models.ForeignKey(StockLot, on_delete=models.PROTECT)
    kind = models.CharField(max_length=40)
    on_hand_delta = models.IntegerField(default=0)
    reserved_delta = models.IntegerField(default=0)
    inspection_delta = models.IntegerField(default=0)
    on_hand_after = models.PositiveIntegerField()
    reserved_after = models.PositiveIntegerField()
    reason = models.CharField(max_length=300)
    reference_id = models.CharField(max_length=36, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at"]
