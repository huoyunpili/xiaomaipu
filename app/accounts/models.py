import uuid
from typing import ClassVar

from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models


class SellerUserManager(UserManager):
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields["role"] = "ADMIN"
        return super().create_superuser(username, email, password, **extra_fields)


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "ADMIN", "管理员"
        OPERATOR = "OPERATOR", "操作员"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    display_name = models.CharField("显示名称", max_length=100, blank=True)
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.OPERATOR)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    objects: ClassVar[SellerUserManager] = SellerUserManager()

    @property
    def is_shop_admin(self):
        return self.is_active and (self.is_superuser or self.role == self.Role.ADMIN)


class LoginAttempt(models.Model):
    key = models.CharField(primary_key=True, max_length=64)
    count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)
