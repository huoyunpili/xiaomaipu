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
    first_connected_at = models.DateTimeField(null=True, blank=True)
    sync_start_at = models.DateTimeField(null=True, blank=True)
    sync_start_basis = models.CharField(max_length=32, blank=True)
    sync_start_confirmed_at = models.DateTimeField(null=True, blank=True)
    sync_start_confirmed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="confirmed_sync_starts",
    )

    @property
    def sync_ready(self):
        return self.sync_start_at is not None


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
    scanned_count = models.PositiveIntegerField(default=0)
    accepted_count = models.PositiveIntegerField(default=0)
    historical_count = models.PositiveIntegerField(default=0)
    review_count = models.PositiveIntegerField(default=0)
    lease_owner = models.CharField(max_length=64, blank=True)
    lease_generation = models.PositiveIntegerField(default=0)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)

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
    class Scope(models.TextChoices):
        IN_SCOPE = "IN_SCOPE", "自动同步范围内"
        HISTORICAL = "HISTORICAL", "接入日前历史"
        UNKNOWN = "UNKNOWN", "范围待核对"

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
    source_created_at = models.DateTimeField(null=True, blank=True)
    scope_status = models.CharField(max_length=20, choices=Scope, default=Scope.UNKNOWN)
    scope_reason = models.CharField(max_length=300, blank=True)
    contract_issues = models.JSONField(default=list)
    auto_matched = models.BooleanField(default=False)

    class Meta:
        ordering = ["-source_updated", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "external_order_no"],
                name="unique_xgj_order",
            )
        ]
        indexes = [models.Index(fields=["connection", "scope_status", "source_updated"])]


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


class ExternalFactApplication(BaseModel):
    class Result(models.TextChoices):
        STORED = "STORED", "已保存待人工处理"
        LINKED = "LINKED", "已匹配本地记录"
        OUT_OF_SCOPE = "OUT_OF_SCOPE", "接入日前历史"
        NEEDS_REVIEW = "NEEDS_REVIEW", "字段待核对"

    connection = models.ForeignKey(
        Connection, on_delete=models.PROTECT, related_name="applications"
    )
    fact_type = models.CharField(max_length=40)
    external_key = models.CharField(max_length=120)
    semantic_version = models.PositiveSmallIntegerField(default=1)
    source_updated = models.PositiveBigIntegerField(default=0)
    payload_hash = models.CharField(max_length=64)
    result = models.CharField(max_length=20, choices=Result)
    target_type = models.CharField(max_length=40, blank=True)
    target_id = models.CharField(max_length=100, blank=True)
    reason = models.CharField(max_length=500, blank=True)
    applied_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-applied_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "fact_type", "external_key", "semantic_version"],
                name="unique_external_fact_application",
            )
        ]
        indexes = [models.Index(fields=["connection", "result", "source_updated"])]


class PlatformRefund(BaseModel):
    connection = models.ForeignKey(Connection, on_delete=models.PROTECT, related_name="refunds")
    platform_order = models.ForeignKey(
        PlatformOrder, on_delete=models.PROTECT, related_name="platform_refunds"
    )
    external_refund_no = models.CharField(max_length=120)
    external_order_no = models.CharField(max_length=100)
    source_updated = models.PositiveBigIntegerField(default=0)
    snapshot = models.JSONField(default=dict)
    contract_issues = models.JSONField(default=list)
    needs_review = models.BooleanField(default=True)

    class Meta:
        ordering = ["-source_updated", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "external_refund_no"], name="unique_platform_refund"
            )
        ]


class PlatformRefundRevision(BaseModel):
    platform_refund = models.ForeignKey(
        PlatformRefund, on_delete=models.PROTECT, related_name="revisions"
    )
    source_updated = models.PositiveBigIntegerField()
    snapshot = models.JSONField(default=dict)

    class Meta:
        ordering = ["-source_updated"]
        constraints = [
            models.UniqueConstraint(
                fields=["platform_refund", "source_updated"],
                name="unique_platform_refund_revision",
            )
        ]
