from django.db import models

from app.common.models import BaseModel


class ListingMapping(BaseModel):
    channel = models.ForeignKey("shops.SalesChannel", on_delete=models.PROTECT)
    external_listing_id = models.CharField(max_length=200)
    external_spec_id = models.CharField(max_length=200, blank=True)
    listing_url = models.URLField(max_length=1000, blank=True)
    sku = models.ForeignKey(
        "catalog.SKU", on_delete=models.PROTECT, related_name="listing_mappings"
    )
    confirmed_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT)
    evidence = models.CharField(max_length=300)

    class Meta:
        ordering = ["channel_id", "external_listing_id", "external_spec_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["channel", "external_listing_id", "external_spec_id"],
                name="unique_listing_spec_mapping",
            ),
            models.CheckConstraint(
                condition=~models.Q(external_listing_id=""), name="listing_id_required"
            ),
        ]


class SupplyAllocation(BaseModel):
    purchase = models.ForeignKey(
        "procurement.Purchase", on_delete=models.PROTECT, related_name="supply_allocations"
    )
    order_item = models.ForeignKey(
        "orders.OrderItem", on_delete=models.PROTECT, related_name="supply_allocations"
    )
    quantity = models.PositiveIntegerField()
    actor = models.ForeignKey("accounts.User", on_delete=models.PROTECT, null=True, blank=True)
    source = models.CharField(max_length=16, default="MANUAL")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["purchase", "order_item"], name="unique_purchase_order_supply"
            ),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="supply_qty_positive"),
        ]


class BottleneckThreshold(BaseModel):
    kind = models.CharField(max_length=4, unique=True)
    days = models.PositiveIntegerField()


class FollowUp(BaseModel):
    kind = models.CharField(max_length=4)
    object_type = models.CharField(max_length=32)
    object_id = models.UUIDField()
    cycle = models.PositiveIntegerField(default=1)
    first_detected_at = models.DateTimeField()
    snoozed_until = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=300, blank=True)
    last_actor = models.ForeignKey("accounts.User", on_delete=models.PROTECT, null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-first_detected_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["kind", "object_type", "object_id"],
                condition=models.Q(resolved_at__isnull=True),
                name="one_active_followup_cycle",
            )
        ]
        indexes = [models.Index(fields=["kind", "resolved_at", "snoozed_until"])]


class CommitmentRevision(BaseModel):
    object_type = models.CharField(max_length=32)
    object_id = models.UUIDField()
    old_due_at = models.DateTimeField(null=True, blank=True)
    new_due_at = models.DateTimeField(null=True, blank=True)
    reason = models.CharField(max_length=300)
    actor = models.ForeignKey("accounts.User", on_delete=models.PROTECT)

    class Meta:
        ordering = ["created_at", "id"]
