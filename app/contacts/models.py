from django.db import models

from app.common.models import BaseModel


class Customer(BaseModel):
    name = models.CharField(max_length=100)
    contact = models.CharField(max_length=200, blank=True)
    tags = models.CharField(max_length=300, blank=True)
    notes = models.TextField(blank=True)
    risk_reason = models.TextField(blank=True)
    suggestion = models.CharField(
        max_length=20,
        default="NONE",
        choices=[
            ("NONE", "无建议"),
            ("PENDING", "售后结束后提醒拉黑"),
            ("DEFERRED", "暂缓处理"),
            ("IGNORED", "已忽略建议"),
            ("DONE", "已自行处理"),
        ],
    )
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self):
        return self.name

    @property
    def has_open_business(self):
        from django.db.models import F

        from app.orders.models import ReturnReceipt, SalesOrder

        return (
            SalesOrder.objects.filter(customer=self)
            .exclude(status__in=["COMPLETED", "CANCELLED"])
            .exists()
            or SalesOrder.objects.filter(customer=self)
            .exclude(received_fen=F("amount_fen") - F("amount_reduction_fen") + F("refunded_fen"))
            .exists()
            or ReturnReceipt.objects.filter(
                reservation__item__order__customer=self, status="INSPECTION"
            ).exists()
        )

    @property
    def suggestion_label(self):
        if self.suggestion == "PENDING":
            return (
                "仍有进行中交易或售后，暂不提醒拉黑"
                if self.has_open_business
                else "交易及售后已结束，可自行决定是否拉黑"
            )
        return self.get_suggestion_display()
