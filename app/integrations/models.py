from django.db import models

from app.common.models import BaseModel


class Connection(BaseModel):
    shop = models.OneToOneField("shops.Shop", on_delete=models.PROTECT)
    seller_id = models.CharField(max_length=100)
    enabled = models.BooleanField(default=False)
    last_success = models.DateTimeField(null=True, blank=True)
    cursor = models.PositiveBigIntegerField(default=0)
    error = models.CharField(max_length=300, blank=True)
    service_info = models.JSONField(default=dict)
    actor = models.ForeignKey("accounts.User", on_delete=models.PROTECT)


class SyncRun(BaseModel):
    connection = models.ForeignKey(Connection, on_delete=models.PROTECT, related_name="runs")
    status = models.CharField(max_length=20, default="QUEUED")
    page = models.PositiveIntegerField(default=1)
    count = models.PositiveIntegerField(default=0)
    attempts = models.PositiveIntegerField(default=0)
    window_start = models.PositiveBigIntegerField(default=0)
    window_end = models.PositiveBigIntegerField(default=0)
    error = models.CharField(max_length=300, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    @property
    def status_label(self):
        return {
            "QUEUED": "等待后台处理",
            "RUNNING": "同步中",
            "RETRY": "等待重试",
            "DONE": "同步完成",
            "FAILED": "同步失败",
        }.get(self.status, self.status)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["connection"],
                condition=models.Q(status__in=["QUEUED", "RUNNING", "RETRY"]),
                name="one_active_xgj_run",
            )
        ]


class PlatformOrder(BaseModel):
    refund_snapshot = models.JSONField(default=dict)
    refund_checked_at = models.DateTimeField(null=True, blank=True)
    connection = models.ForeignKey(Connection, on_delete=models.PROTECT, related_name="orders")
    external_order_no = models.CharField(max_length=100)
    source_updated = models.PositiveBigIntegerField(default=0)
    snapshot = models.JSONField(default=dict)
    order = models.OneToOneField(
        "orders.SalesOrder", on_delete=models.PROTECT, null=True, blank=True
    )
    needs_review = models.BooleanField(default=True)

    class Meta:
        ordering = ["-source_updated", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "external_order_no"],
                name="unique_xgj_order",
            )
        ]


class PushNotice(BaseModel):
    connection = models.ForeignKey(Connection, on_delete=models.PROTECT)
    fingerprint = models.CharField(max_length=64, unique=True)
    external_order_no = models.CharField(max_length=100)
    status = models.CharField(max_length=20, default="PENDING")
    attempts = models.PositiveIntegerField(default=0)
    error = models.CharField(max_length=300, blank=True)
    next_attempt = models.DateTimeField(null=True, blank=True)


class PlatformRevision(BaseModel):
    platform_order = models.ForeignKey(
        PlatformOrder, on_delete=models.PROTECT, related_name="revisions"
    )
    source_updated = models.PositiveBigIntegerField()
    snapshot = models.JSONField(default=dict)

    class Meta:
        ordering = ["-source_updated"]
        constraints = [
            models.UniqueConstraint(
                fields=["platform_order", "source_updated"], name="unique_xgj_revision"
            )
        ]
