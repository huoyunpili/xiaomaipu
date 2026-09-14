import uuid

from django.db import models

from app.catalog.models import ConditionFields
from app.common.models import BaseModel


def purchase_number():
    return "CG" + uuid.uuid4().hex[:16].upper()


class Supplier(BaseModel):
    name = models.CharField(max_length=100)
    contact = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self):
        return self.name


class SupplierQuote(BaseModel, ConditionFields):
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="quotes")
    sku = models.ForeignKey("catalog.SKU", on_delete=models.PROTECT)
    unit_cost_fen = models.PositiveBigIntegerField()

    class Meta:
        ordering = ["-created_at", "id"]


class Purchase(BaseModel, ConditionFields):
    direct = models.BooleanField(default=False)
    direct_qty = models.PositiveIntegerField(default=0)
    order_item = models.ForeignKey(
        "orders.OrderItem",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="purchases",
    )
    number = models.CharField(max_length=20, unique=True, default=purchase_number)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT)
    supplier_name = models.CharField(max_length=100)
    sku = models.ForeignKey("catalog.SKU", on_delete=models.PROTECT)
    product_name = models.CharField(max_length=200)
    quote = models.ForeignKey(SupplierQuote, null=True, blank=True, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    unit_cost_fen = models.PositiveBigIntegerField()
    shipped_qty = models.PositiveIntegerField(default=0)
    received_qty = models.PositiveIntegerField(default=0)
    returned_qty = models.PositiveIntegerField(default=0)
    cancelled_qty = models.PositiveIntegerField(default=0)
    ordered = models.BooleanField(default=False)
    closed = models.BooleanField(default=False)
    paid_fen = models.PositiveBigIntegerField(default=0)
    refunded_fen = models.PositiveBigIntegerField(default=0)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["-created_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(received_qty__gte=models.F("returned_qty")),
                name="purchase_return_within_received",
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1), name="purchase_positive_qty"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    quantity__gte=models.F("received_qty")
                    + models.F("cancelled_qty")
                    + models.F("direct_qty")
                ),
                name="purchase_received_within_qty",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    shipped_qty__gte=models.F("received_qty") + models.F("direct_qty")
                )
                & models.Q(shipped_qty__lte=models.F("quantity")),
                name="purchase_shipped_range",
            ),
            models.CheckConstraint(
                condition=models.Q(paid_fen__gte=models.F("refunded_fen")),
                name="purchase_refund_within_paid",
            ),
        ]

    @property
    def pending_qty(self):
        return self.quantity - self.received_qty - self.cancelled_qty - self.direct_qty

    @property
    def unallocated_qty(self):
        return sum(
            min(receipt.quantity - receipt.returned_qty, receipt.lot.available_qty)
            for receipt in self.receipts.select_related("lot")
        )

    @property
    def planned_qty(self):
        return self.pending_qty + self.unallocated_qty

    @property
    def in_transit_qty(self):
        return 0 if self.closed else self.shipped_qty - self.received_qty - self.direct_qty

    @property
    def total_fen(self):
        return (self.quantity - self.cancelled_qty - self.returned_qty) * self.unit_cost_fen

    @property
    def net_paid_fen(self):
        return self.paid_fen - self.refunded_fen

    @property
    def payable_fen(self):
        return max(self.total_fen - self.net_paid_fen, 0)

    @property
    def refund_due_fen(self):
        return max(self.net_paid_fen - self.total_fen, 0)

    @property
    def status_label(self):
        if self.direct and self.direct_qty:
            return "供应商已直发" if not self.pending_qty else "部分已直发"
        if self.closed:
            return "已关闭剩余采购" if self.received_qty else "已取消"
        if self.received_qty == self.quantity:
            return "已到货"
        if self.received_qty:
            return "部分到货"
        if self.shipped_qty:
            return "供应商已发货"
        return "已下单" if self.ordered else "待下单"


class PurchaseReceipt(BaseModel):
    purchase = models.ForeignKey(Purchase, on_delete=models.PROTECT, related_name="receipts")
    lot = models.OneToOneField("inventory.StockLot", on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    returned_qty = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=models.F("returned_qty")),
                name="purchase_receipt_return_range",
            )
        ]

    @property
    def returnable_qty(self):
        return min(self.quantity - self.returned_qty, self.lot.available_qty)


class PurchaseEvent(BaseModel):
    purchase = models.ForeignKey(Purchase, on_delete=models.PROTECT, related_name="events")
    kind = models.CharField(max_length=30)
    quantity = models.PositiveIntegerField(default=0)
    amount_fen = models.PositiveBigIntegerField(default=0)
    reason = models.CharField(max_length=300)
    receipt = models.ForeignKey(PurchaseReceipt, null=True, blank=True, on_delete=models.PROTECT)

    class Meta:
        ordering = ["-created_at", "id"]
