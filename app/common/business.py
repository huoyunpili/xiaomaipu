from app.audit.models import AuditEvent
from app.common.models import OutboxEvent


class BusinessError(Exception):
    pass


def whole(value, name, minimum=0):
    if type(value) is not int or not minimum <= value <= 10**12:
        raise BusinessError(f"{name}必须是大于等于 {minimum} 的整数，且不超过系统上限。")
    return value


def record_event(actor, action, instance, request_id="", **details):
    AuditEvent.objects.create(
        actor=actor,
        action=action,
        object_id=str(instance.pk),
        request_id=request_id,
        details=details,
    )
    OutboxEvent.objects.create(topic=action, payload={"object_id": str(instance.pk), **details})
