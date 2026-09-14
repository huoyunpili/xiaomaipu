from django.db import models

from app.common.models import BaseModel


class EvidenceVideo(BaseModel):
    order = models.ForeignKey("orders.SalesOrder", on_delete=models.PROTECT, related_name="videos")
    storage_name = models.CharField(max_length=200)
    original_name = models.CharField(max_length=200)
    size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    mime_type = models.CharField(max_length=60)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["order"], condition=models.Q(active=True), name="one_active_order_video"
            )
        ]
