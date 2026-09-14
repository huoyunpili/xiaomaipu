from django.db import models

from app.common.models import BaseModel


class Shop(BaseModel):
    name = models.CharField("店铺名称", max_length=100)
    platform = models.CharField(max_length=20, default="XIANYU", editable=False)
    external_shop_id = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["is_active"], condition=models.Q(is_active=True), name="one_active_shop"
            )
        ]


class SalesChannel(BaseModel):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=100)
    is_platform = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
