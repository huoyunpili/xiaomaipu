import re
import secrets
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import models
from django.utils import timezone

from app.common.models import BaseModel


class ProductCost(BaseModel):
    shop = models.ForeignKey("shops.Shop", null=True, on_delete=models.PROTECT)
    key = models.CharField(max_length=64)
    title = models.CharField(max_length=200)
    spec = models.CharField(max_length=200, blank=True)
    condition = models.CharField(max_length=100, blank=True)
    unit_fen = models.PositiveBigIntegerField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    supplier = models.CharField(max_length=100, blank=True)
    supplier_wechat = models.CharField(max_length=100, blank=True)
    shipping_note = models.CharField(max_length=1000, blank=True)

    @property
    def display_condition(self):
        match = re.search(r"(?:^|[;；])\s*成色\s*[:：]([^;；]+)", self.spec)
        return self.condition or (match.group(1).strip() if match else "")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["shop", "key"], name="wb_product_shop_key")]


class CostVersion(BaseModel):
    product = models.ForeignKey(ProductCost, related_name="revisions", on_delete=models.PROTECT)
    version = models.PositiveIntegerField()
    unit_fen = models.PositiveBigIntegerField()
    effective_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["product", "version"], name="wb_cost_version_unique")
        ]
        ordering = ["effective_at", "version"]


class Trade(BaseModel):
    class Status(models.TextChoices):
        SHIPPING = "SHIPPING", "待发货"
        PENDING = "PENDING", "待完成"
        REFUNDING = "REFUNDING", "退款处理中"
        COMPLETED = "COMPLETED", "已完成"
        REFUNDED = "REFUNDED", "已退款"
        CLOSED = "CLOSED", "已关闭"
        REVIEW = "REVIEW", "待核对"
        UNPAID = "UNPAID", "未付款"

    shop = models.ForeignKey("shops.Shop", on_delete=models.PROTECT)
    platform = models.OneToOneField(
        "integrations.PlatformOrder", null=True, blank=True, on_delete=models.PROTECT
    )
    number = models.CharField(max_length=100)
    source = models.CharField(max_length=12, default="API")
    status = models.CharField(max_length=16, choices=Status, default=Status.REVIEW)
    status_changed_at = models.DateTimeField(default=timezone.now)
    issue = models.CharField(max_length=500, blank=True)
    product = models.ForeignKey(ProductCost, null=True, on_delete=models.PROTECT)
    title = models.CharField(max_length=200, blank=True)
    spec = models.CharField(max_length=200, blank=True)
    quantity = models.PositiveIntegerField(default=1)
    image = models.URLField(max_length=1000, blank=True)
    paid_fen = models.PositiveBigIntegerField(default=0)
    unit_cost_fen = models.PositiveBigIntegerField(null=True, blank=True)
    cost_version = models.PositiveIntegerField(null=True, blank=True)
    supplier = models.CharField(max_length=100, blank=True)
    supplier_override = models.BooleanField(default=False)
    supplier_wechat = models.CharField(max_length=100, blank=True)
    default_shipping_note = models.CharField(max_length=1000, blank=True)
    platform_note = models.CharField(max_length=1000, blank=True)
    receiver = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=100, blank=True)
    address = models.CharField(max_length=1000, blank=True)
    note = models.CharField(max_length=1000, blank=True)
    waybill = models.CharField(max_length=100, blank=True)
    ordered_at = models.DateTimeField(null=True)
    paid_at = models.DateTimeField(null=True)
    shipped_at = models.DateTimeField(null=True)
    completed_at = models.DateTimeField(null=True)
    refunded_at = models.DateTimeField(null=True)
    refund_applied_at = models.DateTimeField(null=True, blank=True)
    refund_type = models.PositiveSmallIntegerField(null=True, blank=True)
    refunded_fen = models.PositiveBigIntegerField(null=True, blank=True)
    refund_success_confirmed_at = models.DateTimeField(null=True, blank=True)
    synced_at = models.DateTimeField(null=True)
    refund_waybill = models.CharField(max_length=100, blank=True)
    refund_note = models.CharField(max_length=1000, blank=True)
    recovered_at = models.DateTimeField(null=True, blank=True)
    loss_fen = models.PositiveBigIntegerField(default=0)
    loss_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["shop", "number"], name="wb_trade_unique")]
        indexes = [
            models.Index(fields=["status", "paid_at"]),
            models.Index(fields=["completed_at"]),
        ]

    @property
    def display_condition(self):
        match = re.search(r"(?:^|[;；])\s*成色\s*[:：]([^;；]+)", self.spec)
        return (match.group(1).strip() if match else "") or (
            self.product.display_condition if self.product is not None else ""
        )

    @property
    def cost_fen(self):
        return None if self.unit_cost_fen is None else self.unit_cost_fen * self.quantity

    @property
    def fee_fen(self):
        if self.status not in ("SHIPPING", "PENDING", "COMPLETED"):
            return 0
        return int(
            (Decimal(self.paid_fen) * Decimal("0.016")).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        )

    @property
    def profit_fen(self):
        if self.status not in ("SHIPPING", "PENDING", "COMPLETED") or not self.paid_at:
            return None
        return None if self.cost_fen is None else self.paid_fen - self.cost_fen - self.fee_fen

    @property
    def guarantee_fen(self):
        return (
            self.paid_fen
            if self.paid_at and self.status in ("SHIPPING", "PENDING", "REFUNDING")
            else 0
        )

    @property
    def shipping_note(self):
        return "\n".join(
            dict.fromkeys(
                value
                for value in (self.default_shipping_note, self.platform_note, self.note)
                if value
            )
        )

    @property
    def reference_days(self):
        config = getattr(self.shop, "workspacesettings", None)
        return config.reference_days if config else 10

    @property
    def reference_at(self):
        return (
            self.shipped_at + timedelta(days=self.reference_days)
            if self.shipped_at and self.status == "PENDING"
            else None
        )

    @property
    def recovery_fen(self):
        return self.cost_fen if self.status == "REFUNDED" and not self.recovered_at else 0

    @property
    def return_required(self):
        return {1: "无需退货", 2: "需要退货"}.get(self.refund_type or 0, "待核对")

    @property
    def refund_progress(self):
        if self.status != "REFUNDING" or not self.platform_id:
            return ""
        from .services import effective_order_data

        code = effective_order_data(self.platform).get("refund_status")
        return {
            1: "等待卖家处理退款申请",
            2: "等待买家退货",
            3: "买家已退货，等待卖家确认收货",
            8: "等待卖家确认退货地址",
        }.get(code, "退款处理中，请核对平台进度")


class ExportBatch(BaseModel):
    shop = models.ForeignKey("shops.Shop", on_delete=models.PROTECT)
    kind = models.CharField(max_length=12)
    supplier = models.CharField(max_length=100)
    snapshot = models.JSONField(default=list)
    actor = models.ForeignKey("accounts.User", on_delete=models.PROTECT)
    stale = models.BooleanField(default=False)


class WorkspaceSettings(BaseModel):
    shop = models.OneToOneField("shops.Shop", on_delete=models.PROTECT)
    image_retention_days = models.PositiveIntegerField(null=True, blank=True)
    reference_days = models.PositiveIntegerField(default=10)
    supplier_base_url = models.URLField(blank=True, max_length=300)


class ExportImage(BaseModel):
    batch = models.ForeignKey(ExportBatch, related_name="images", on_delete=models.PROTECT)
    page = models.PositiveIntegerField()
    storage_name = models.CharField(max_length=200)
    sha256 = models.CharField(max_length=64)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["batch", "page"], name="wb_export_image_page")
        ]


def supplier_token():
    return secrets.token_urlsafe(32)


class SupplierAccess(BaseModel):
    batch = models.OneToOneField(ExportBatch, on_delete=models.PROTECT, related_name="access")
    token = models.CharField(max_length=64, unique=True, default=supplier_token)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)


class SupplierDispatch(BaseModel):
    trade = models.OneToOneField(Trade, on_delete=models.PROTECT, related_name="dispatch")
    access = models.ForeignKey(SupplierAccess, on_delete=models.PROTECT)
    express_code = models.CharField(max_length=100)
    express_name = models.CharField(max_length=100)
    waybill = models.CharField(max_length=100)
    state = models.CharField(max_length=16, default="READY")
    message = models.CharField(max_length=300, blank=True)
    submitted_at = models.DateTimeField(null=True)
    confirmed_at = models.DateTimeField(null=True)


class SupplierVideo(BaseModel):
    trade = models.ForeignKey(Trade, on_delete=models.PROTECT, related_name="supplier_videos")
    access = models.ForeignKey(SupplierAccess, on_delete=models.PROTECT)
    storage_name = models.CharField(max_length=250)
    original_name = models.CharField(max_length=200)
    size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    mime_type = models.CharField(max_length=50)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["trade", "sha256"], name="supplier_video_hash")
        ]
