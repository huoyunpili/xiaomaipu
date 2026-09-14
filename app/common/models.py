import uuid

from django.db import models


class BaseModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class IdempotencyRecord(BaseModel):
    scope = models.CharField(max_length=160)
    key = models.UUIDField()
    request_hash = models.CharField(max_length=64)
    result = models.JSONField(default=dict)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["scope", "key"], name="unique_idempotency_key")
        ]


class OutboxEvent(BaseModel):
    topic = models.CharField(max_length=100)
    payload_version = models.PositiveSmallIntegerField(default=1)
    payload = models.JSONField(default=dict)
    published_at = models.DateTimeField(null=True, blank=True)
