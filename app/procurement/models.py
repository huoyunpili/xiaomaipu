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
    promised_dispatch_at = models.DateTimeField(null=True, blank=True)
    legacy_logistics = models.BooleanField(default=False)
    rejected_returned_qty = models.PositiveIntegerField(default=0)
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
                condition=models.Q(shipped_qty__lte=models.F("quantity")),
                name="purchase_dispatched_within_qty",
            ),
            models.CheckConstraint(
                condition=models.Q(paid_fen__gte=models.F("refunded_fen")),
                name="purchase_refund_within_paid",
            ),
        ]

    @property
    def pending_qty(self):
        if self.direct:
            return self.quantity - self.cancelled_qty - self.direct_qty
        return max(self.quantity - self.cancelled_qty - self.arrived_qty, 0)

    @property
    def arrived_qty(self):
        return sum(a.quantity for a in self.arrivals.all())

    @property
    def awaiting_inspection_qty(self):
        return sum(a.quantity - a.inspected_qty for a in self.arrivals.all() if not a.customer)

    @property
    def unmatched_qty(self):
        return sum(a.quantity for a in self.arrivals.all() if a.dispatch_id is None)

    @property
    def unshipped_qty(self):
        return max(self.quantity - self.cancelled_qty - self.shipped_qty - self.unmatched_qty, 0)

    @property
    def unallocated_qty(self):
        return sum(
            min(receipt.quantity - receipt.returned_qty, receipt.lot.available_qty)
            for receipt in self.receipts.select_related("lot")
        )

    @property
    def planned_qty(self):
        lost = sum(d.disposed_qty for d in self.dispatches.all())
        return max(self.pending_qty - lost, 0) + self.awaiting_inspection_qty + self.unallocated_qty

    @property
    def in_transit_qty(self):
        return sum(d.remaining_qty for d in self.dispatches.all())

    @property
    def total_fen(self):
        return (
            self.quantity - self.cancelled_qty - self.returned_qty - self.rejected_returned_qty
        ) * self.unit_cost_fen

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
        if self.legacy_logistics:
            return "历史发运待核对"
        if self.awaiting_inspection_qty:
            return "到货待验收"
        if self.in_transit_qty:
            return "部分在途" if self.arrived_qty else "运输中"
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


class PurchaseDispatch(BaseModel):
    purchase = models.ForeignKey(Purchase, on_delete=models.PROTECT, related_name="dispatches")
    quantity = models.PositiveIntegerField()
    received_qty = models.PositiveIntegerField(default=0)
    disposed_qty = models.PositiveIntegerField(default=0)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    expected_arrival_at = models.DateTimeField(null=True, blank=True, db_index=True)
    carrier = models.CharField(max_length=100, blank=True)
    tracking_no = models.CharField(max_length=100, blank=True)
    customer = models.BooleanField(default=False)
    shipment = models.OneToOneField(
        "orders.Shipment", on_delete=models.PROTECT, null=True, blank=True
    )
    source = models.CharField(max_length=16, default="MANUAL")

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0), name="dispatch_positive_qty"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    quantity__gte=models.F("received_qty") + models.F("disposed_qty")
                ),
                name="dispatch_quantity_balance",
            ),
        ]

    @property
    def remaining_qty(self):
        return self.quantity - self.received_qty - self.disposed_qty

    def __str__(self):
        return f"{self.tracking_no or '未填运单'} · 待收 {self.remaining_qty} 件"


class PurchaseArrival(BaseModel):
    purchase = models.ForeignKey(Purchase, on_delete=models.PROTECT, related_name="arrivals")
    dispatch = models.ForeignKey(
        PurchaseDispatch, on_delete=models.PROTECT, null=True, blank=True, related_name="arrivals"
    )
    quantity = models.PositiveIntegerField()
    inspected_qty = models.PositiveIntegerField(default=0)
    rejected_qty = models.PositiveIntegerField(default=0)
    rejected_returned_qty = models.PositiveIntegerField(default=0)
    received_at = models.DateTimeField(null=True, blank=True)
    customer = models.BooleanField(default=False)
    source = models.CharField(max_length=16, default="MANUAL")

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="arrival_positive_qty"),
            models.CheckConstraint(
                condition=models.Q(quantity__gte=models.F("inspected_qty")),
                name="arrival_inspection_balance",
            ),
            models.CheckConstraint(
                condition=models.Q(inspected_qty__gte=models.F("rejected_qty"))
                & models.Q(rejected_qty__gte=models.F("rejected_returned_qty")),
                name="arrival_reject_balance",
            ),
        ]

    @property
    def pending_qty(self):
        return 0 if self.customer else self.quantity - self.inspected_qty

    @property
    def pending_return_qty(self):
        return self.rejected_qty - self.rejected_returned_qty

    def __str__(self):
        return f"收到 {self.quantity} 件 · 待验 {self.pending_qty} 件"


class PurchaseInspection(BaseModel):
    arrival = models.ForeignKey(
        PurchaseArrival, on_delete=models.PROTECT, related_name="inspections"
    )
    quantity = models.PositiveIntegerField()
    result = models.CharField(
        max_length=12, choices=[("ACCEPT", "验收合格"), ("REJECT", "不合格待退供")]
    )
    inspected_at = models.DateTimeField(null=True, blank=True)
    receipt = models.OneToOneField(
        PurchaseReceipt, on_delete=models.PROTECT, null=True, blank=True, related_name="inspection"
    )
    reason = models.CharField(max_length=300)
    source = models.CharField(max_length=16, default="MANUAL")

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0), name="inspection_positive_qty"
            )
        ]


class DispatchDisposition(BaseModel):
    dispatch = models.ForeignKey(
        PurchaseDispatch, on_delete=models.PROTECT, related_name="dispositions"
    )
    quantity = models.PositiveIntegerField()
    reason = models.CharField(max_length=300)
    occurred_at = models.DateTimeField(null=True, blank=True)
    resolved = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0), name="disposition_positive_qty"
            )
        ]
