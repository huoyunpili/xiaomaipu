import uuid

from django.db import models
from django.utils import timezone

from app.common.models import BaseModel


def order_number():
    return timezone.localdate().strftime("SO%Y%m%d") + uuid.uuid4().hex[:12].upper()


class SalesOrder(BaseModel):
    customer = models.ForeignKey(
        "contacts.Customer", on_delete=models.PROTECT, null=True, blank=True, related_name="orders"
    )

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "草稿"
        CONFIRMED = "CONFIRMED", "已确认待备货"
        PARTIAL = "PARTIAL", "部分已发货"
        SHIPPED = "SHIPPED", "已发货"
        COMPLETED = "COMPLETED", "已完成"
        CANCELLED = "CANCELLED", "已取消"

    number = models.CharField(max_length=24, default=order_number, unique=True)
    channel = models.ForeignKey("shops.SalesChannel", on_delete=models.PROTECT)
    customer_name = models.CharField("客户称呼/临时标识", max_length=100)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    external_order_no = models.CharField(max_length=100, blank=True)
    platform_paid = models.BooleanField(default=False)
    amount_fen = models.PositiveBigIntegerField()
    amount_reduction_fen = models.PositiveBigIntegerField(default=0)
    received_fen = models.PositiveBigIntegerField(default=0)
    refunded_fen = models.PositiveBigIntegerField(default=0)
    cost_fen = models.PositiveBigIntegerField(default=0)
    fees_fen = models.PositiveBigIntegerField(default=0)
    recovered_cost_fen = models.PositiveBigIntegerField(default=0)
    realized_profit_fen = models.BigIntegerField(null=True, blank=True)
    carrier = models.CharField(max_length=100, blank=True)
    tracking_no = models.CharField(max_length=100, blank=True)
    delivery_method = models.CharField(max_length=20, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["channel", "external_order_no"],
                condition=~models.Q(external_order_no=""),
                name="unique_channel_order",
            ),
            models.CheckConstraint(
                condition=models.Q(received_fen__gte=models.F("refunded_fen")),
                name="refund_within_receipts",
            ),
            models.CheckConstraint(
                condition=models.Q(amount_fen__gte=models.F("amount_reduction_fen")),
                name="reduction_within_order_amount",
            ),
        ]

    @property
    def adjusted_due_fen(self):
        return self.amount_fen - self.amount_reduction_fen

    @property
    def net_received_fen(self):
        return self.received_fen - self.refunded_fen

    @property
    def outstanding_fen(self):
        return max(self.adjusted_due_fen - self.net_received_fen, 0)

    @property
    def payment_label(self):
        net = self.net_received_fen
        if net > self.adjusted_due_fen:
            return "超额收款待处理"
        if self.refunded_fen and net == 0:
            return "已退款"
        if self.refunded_fen:
            return "部分退款"
        if net == self.adjusted_due_fen:
            return "已收齐"
        if net:
            return "部分收款"
        if self.platform_paid:
            return "平台托管待结算"
        return "未收款"


class OrderItem(BaseModel):
    order = models.ForeignKey(SalesOrder, on_delete=models.PROTECT, related_name="items")
    sku = models.ForeignKey("catalog.SKU", on_delete=models.PROTECT)
    selected_lot = models.ForeignKey(
        "inventory.StockLot", on_delete=models.PROTECT, null=True, blank=True
    )
    quantity = models.PositiveIntegerField()
    unit_price_fen = models.PositiveBigIntegerField()
    title_snapshot = models.CharField(max_length=200)
    specification_snapshot = models.CharField(max_length=200, blank=True)
    condition_snapshot = models.JSONField(default=dict)
    reserved_qty = models.PositiveIntegerField(default=0)
    shipped_qty = models.PositiveIntegerField(default=0)
    cancelled_qty = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0), name="positive_order_quantity"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    quantity__gte=models.F("reserved_qty")
                    + models.F("shipped_qty")
                    + models.F("cancelled_qty")
                ),
                name="order_item_quantity_balance",
            ),
        ]

    @property
    def shortage_qty(self):
        return max(self.quantity - self.reserved_qty - self.shipped_qty - self.cancelled_qty, 0)


class Reservation(BaseModel):
    shipment = models.ForeignKey(
        "orders.Shipment",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reservations",
    )
    item = models.ForeignKey(OrderItem, on_delete=models.PROTECT, related_name="reservations")
    lot = models.ForeignKey("inventory.StockLot", on_delete=models.PROTECT, null=True, blank=True)
    quantity = models.PositiveIntegerField()
    status = models.CharField(max_length=16, default="ACTIVE")
    unit_cost_fen = models.PositiveBigIntegerField()
    unit_freight_fen = models.PositiveBigIntegerField()
    condition_snapshot = models.JSONField(default=dict)
    returned_qty = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=models.F("returned_qty")),
                name="return_within_shipped",
            ),
            models.CheckConstraint(
                condition=models.Q(lot__isnull=False)
                | models.Q(status="CONSUMED", shipment__isnull=False),
                name="reservation_source_present",
            ),
        ]


class Shipment(BaseModel):
    purchase = models.ForeignKey(
        "procurement.Purchase", null=True, blank=True, on_delete=models.PROTECT
    )
    evidence_note = models.CharField(max_length=300, blank=True)
    order = models.ForeignKey(SalesOrder, on_delete=models.PROTECT, related_name="shipments")
    quantity = models.PositiveIntegerField()
    delivery_method = models.CharField(max_length=20)
    carrier = models.CharField(max_length=100, blank=True)
    tracking_no = models.CharField(max_length=100, blank=True)
    cost_fen = models.PositiveBigIntegerField(default=0)
    fee_fen = models.PositiveBigIntegerField(default=0)
    completed = models.BooleanField(default=False)

    class Meta:
        ordering = ["created_at", "id"]


class OrderEvent(BaseModel):
    order = models.ForeignKey(SalesOrder, on_delete=models.PROTECT, related_name="events")
    action = models.CharField(max_length=100)
    description = models.CharField(max_length=300)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at"]


class ReturnReceipt(BaseModel):
    inspection_lot = models.ForeignKey(
        "inventory.StockLot",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="inspection_returns",
    )
    reservation = models.ForeignKey(Reservation, on_delete=models.PROTECT, related_name="returns")
    quantity = models.PositiveIntegerField()
    status = models.CharField(max_length=20, default="INSPECTION")
    reason = models.CharField(max_length=300)
    restocked_lot = models.ForeignKey("inventory.StockLot", on_delete=models.PROTECT, null=True)
