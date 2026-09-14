from django.db import models

from app.common.models import BaseModel


class MoneyEntry(BaseModel):
    order = models.ForeignKey(
        "orders.SalesOrder", on_delete=models.PROTECT, related_name="money_entries"
    )
    kind = models.CharField(max_length=20)
    amount_fen = models.PositiveBigIntegerField()
    reduction_fen = models.PositiveBigIntegerField(default=0)
    reason = models.CharField(max_length=300)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at"]


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
