import hashlib
import json
from collections.abc import Callable
from typing import Any
from uuid import UUID

from django.db import transaction

from .models import IdempotencyRecord


class IdempotencyConflict(Exception):
    pass


@transaction.atomic
def execute_once(
    scope: str, key: UUID, payload: dict, action: Callable[[], dict]
) -> dict[str, Any]:
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    record, created = IdempotencyRecord.objects.get_or_create(
        scope=scope, key=key, defaults={"request_hash": digest}
    )
    record = IdempotencyRecord.objects.select_for_update().get(pk=record.pk)
    if record.request_hash != digest:
        raise IdempotencyConflict("同一提交标识对应的内容已变化，请刷新后重试。")
    if not created:
        return record.result
    result = action()
    record.result = result
    record.save(update_fields=["result", "updated_at"])
    return result
