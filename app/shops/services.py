from django.db import transaction

from app.accounts.policies import require_admin
from app.audit.models import AuditEvent
from app.common.models import OutboxEvent
from app.common.services import execute_once

from .models import Shop


class StaleVersion(Exception):
    pass


def update_shop(*, actor, name, version, submission_key, request_id=""):
    require_admin(actor)
    name = name.strip()
    if not name or len(name) > 100:
        raise ValueError("店铺名称须为 1～100 个字符。")

    @transaction.atomic
    def action():
        shop = Shop.objects.select_for_update().get(is_active=True)
        if shop.version != version:
            raise StaleVersion("店铺资料已被更新，请刷新后再修改。")
        shop.name = name
        shop.version += 1
        shop.save(update_fields=["name", "version", "updated_at"])
        AuditEvent.objects.create(
            actor=actor,
            action="shop.updated",
            object_id=str(shop.pk),
            request_id=request_id,
            details={"changed_fields": ["name"], "version": shop.version},
        )
        OutboxEvent.objects.create(
            topic="shop.updated", payload={"shop_id": str(shop.pk), "version": shop.version}
        )
        return {"shop_id": str(shop.pk), "version": shop.version}

    return execute_once(
        f"shop.update:{actor.pk}", submission_key, {"name": name, "version": version}, action
    )
