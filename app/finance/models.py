from django.db import models

from app.common.models import BaseModel


class MoneyEntry(BaseModel):
    order = models.ForeignKey(
        "orders.SalesOrder",
        on_delete=models.PROTECT,
        related_name="money_entries",
        null=True,
        blank=True,
    )
    purchase = models.ForeignKey(
        "procurement.Purchase",
        on_delete=models.PROTECT,
        related_name="money_entries",
        null=True,
        blank=True,
    )
    purchase_event = models.OneToOneField(
        "procurement.PurchaseEvent",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="cash_entry",
    )
    direction = models.CharField(
        max_length=3, choices=[("IN", "流入"), ("OUT", "流出")], default="OUT"
    )
    account_type = models.CharField(
        max_length=16,
        default="UNKNOWN",
        choices=[
            ("UNKNOWN", "未记录"),
            ("BANK", "银行卡"),
            ("ALIPAY", "支付宝"),
            ("WECHAT", "微信"),
            ("CASH", "现金"),
        ],
    )
    actor = models.ForeignKey("accounts.User", on_delete=models.PROTECT, null=True, blank=True)
    source = models.CharField(max_length=16, default="MANUAL")
    source_ref = models.CharField(max_length=200, blank=True)
    time_quality = models.CharField(max_length=16, default="UNKNOWN")
    kind = models.CharField(max_length=20)
    amount_fen = models.PositiveBigIntegerField()
    reduction_fen = models.PositiveBigIntegerField(default=0)
    reason = models.CharField(max_length=300)
    occurred_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(order__isnull=False, purchase__isnull=True)
                    | models.Q(order__isnull=True, purchase__isnull=False)
                ),
                name="money_exactly_one_document",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(order__isnull=False, kind="RECEIPT", direction="IN")
                    | models.Q(order__isnull=False, kind__in=["REFUND", "FEE"], direction="OUT")
                    | models.Q(purchase__isnull=False, kind="PAYMENT", direction="OUT")
                    | models.Q(purchase__isnull=False, kind="REFUND", direction="IN")
                ),
                name="money_kind_direction",
            ),
        ]


class ProfitSnapshot(BaseModel):
    order = models.ForeignKey(
        "orders.SalesOrder", on_delete=models.PROTECT, related_name="profit_snapshots"
    )
    net_received_fen = models.BigIntegerField()
    due_fen = models.BigIntegerField()
    cost_fen = models.BigIntegerField()
    fees_fen = models.BigIntegerField()
    recovered_cost_fen = models.BigIntegerField()
    realized_profit_fen = models.BigIntegerField(null=True)
    trigger = models.CharField(max_length=100)


class CustomerPaymentFact(BaseModel):
    order = models.ForeignKey(
        "orders.SalesOrder", on_delete=models.PROTECT, related_name="customer_payments"
    )
    amount_fen = models.PositiveBigIntegerField()
    occurred_at = models.DateTimeField(null=True, blank=True)
    actor = models.ForeignKey("accounts.User", on_delete=models.PROTECT, null=True, blank=True)
    source = models.CharField(max_length=16, default="MANUAL")
    source_ref = models.CharField(max_length=200)
    time_quality = models.CharField(max_length=16, default="UNKNOWN")
    platform_status = models.CharField(max_length=100, blank=True)
    evidence = models.CharField(max_length=300)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["order", "source", "source_ref"], name="unique_customer_payment_fact"
            ),
            models.CheckConstraint(
                condition=models.Q(amount_fen__gt=0), name="customer_payment_positive"
            ),
            models.CheckConstraint(
                condition=~models.Q(source_ref=""), name="customer_payment_source_required"
            ),
        ]
