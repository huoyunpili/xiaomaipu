from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from app.accounts.policies import require_admin, require_operator
from app.common.business import BusinessError, record_event, whole
from app.common.services import execute_once
from app.orders.models import OrderItem, SalesOrder
from app.procurement.models import Purchase

from .models import BottleneckThreshold, FollowUp, ListingMapping, SupplyAllocation
from .projections import DEFAULT_THRESHOLDS


def save_listing_mapping(
    *,
    actor,
    submission_key,
    sku_id,
    channel_id,
    external_listing_id,
    external_spec_id="",
    listing_url="",
    evidence="",
    request_id="",
):
    require_operator(actor)
    listing_id = external_listing_id.strip()
    spec_id = external_spec_id.strip()
    if not listing_id or not evidence.strip():
        raise BusinessError("请填写商品编号和确认依据。")

    def action():
        mapping, created = ListingMapping.objects.select_for_update().get_or_create(
            channel_id=channel_id,
            external_listing_id=listing_id,
            external_spec_id=spec_id,
            defaults={
                "sku_id": sku_id,
                "listing_url": listing_url,
                "confirmed_by": actor,
                "evidence": evidence.strip(),
            },
        )
        if not created and mapping.sku_id != sku_id:
            raise BusinessError("该渠道商品和规格已映射到另一 SKU；请先核对，不能静默覆盖。")
        if not created:
            mapping.listing_url = listing_url
            mapping.evidence = evidence.strip()
            mapping.confirmed_by = actor
            mapping.save()
        record_event(actor, "listing.mapping_confirmed", mapping, request_id)
        return {"sku_id": str(sku_id), "mapping_id": str(mapping.pk)}

    return execute_once(
        f"listing.mapping:{actor.pk}",
        submission_key,
        dict(
            sku_id=str(sku_id),
            channel_id=str(channel_id),
            external_listing_id=listing_id,
            external_spec_id=spec_id,
            listing_url=listing_url,
            evidence=evidence,
        ),
        action,
    )


def allocate_supply(*, actor, submission_key, purchase_id, order_item_id, quantity, request_id=""):
    require_operator(actor)
    whole(quantity, "计划供给数量", 1)

    def action():
        original_item = OrderItem.objects.select_related("order").get(pk=order_item_id)
        order = SalesOrder.objects.select_for_update().get(pk=original_item.order_id)
        item = OrderItem.objects.get(pk=order_item_id)
        purchase = Purchase.objects.select_for_update().get(pk=purchase_id)
        if order.status not in (SalesOrder.Status.CONFIRMED, SalesOrder.Status.PARTIAL):
            raise BusinessError("只能为待发货订单安排供货。")
        if item.sku_id != purchase.sku_id:
            raise BusinessError("采购商品与销售商品不一致。")
        current = SupplyAllocation.objects.filter(purchase=purchase, order_item=item).first()
        previous = current.quantity if current else 0
        purchase_other = sum(
            a.quantity
            for a in SupplyAllocation.objects.filter(purchase=purchase).exclude(order_item=item)
        )
        item_other = sum(
            a.quantity
            for a in SupplyAllocation.objects.filter(order_item=item).exclude(purchase=purchase)
        )
        if purchase_other + quantity > purchase.quantity - purchase.cancelled_qty:
            raise BusinessError("计划供给总量超过采购有效数量。")
        if item_other + quantity > item.shortage_qty + previous:
            raise BusinessError("计划供给总量超过销售订单当前缺口。")
        if purchase.direct and purchase.order_item_id != item.pk:
            raise BusinessError("直发采购只能供给创建时关联的销售订单。")
        allocation, _ = SupplyAllocation.objects.update_or_create(
            purchase=purchase,
            order_item=item,
            defaults={"quantity": quantity, "actor": actor, "source": "MANUAL"},
        )
        record_event(actor, "supply.allocated", allocation, request_id, quantity=quantity)
        return {"purchase_id": str(purchase.pk), "allocation_id": str(allocation.pk)}

    return execute_once(
        f"supply.allocate:{actor.pk}",
        submission_key,
        dict(purchase_id=str(purchase_id), order_item_id=str(order_item_id), quantity=quantity),
        action,
    )


@transaction.atomic
def sync_followups(cards, as_of=None):
    as_of = as_of or timezone.now()
    # A stable row lock serializes first detection when two local requests open the workbench.
    BottleneckThreshold.objects.select_for_update().order_by("kind").first()
    keys = {(card["kind"], card["object_type"], card["object_id"]) for card in cards}
    active = list(FollowUp.objects.select_for_update().filter(resolved_at__isnull=True))
    resolved = []
    for followup in active:
        if (followup.kind, followup.object_type, followup.object_id) not in keys:
            followup.resolved_at = as_of
            followup.updated_at = as_of
            resolved.append(followup)
    if resolved:
        FollowUp.objects.bulk_update(resolved, ["resolved_at", "updated_at"])
    active_map = {
        (row.kind, row.object_type, row.object_id): row
        for row in FollowUp.objects.filter(resolved_at__isnull=True)
    }
    cycles = {
        (row["kind"], row["object_type"], row["object_id"]): row["value"]
        for row in FollowUp.objects.values("kind", "object_type", "object_id").annotate(
            value=Max("cycle")
        )
    }
    missing = []
    for key in keys - active_map.keys():
        missing.append(
            FollowUp(
                kind=key[0],
                object_type=key[1],
                object_id=key[2],
                cycle=cycles.get(key, 0) + 1,
                first_detected_at=as_of,
            )
        )
    if missing:
        FollowUp.objects.bulk_create(missing)
        active_map = {
            (row.kind, row.object_type, row.object_id): row
            for row in FollowUp.objects.filter(resolved_at__isnull=True)
        }
    for card in cards:
        key = (card["kind"], card["object_type"], card["object_id"])
        current_followup = active_map.get(key)
        assert current_followup is not None
        card["followup"] = current_followup
        card["snoozed"] = bool(
            current_followup.snoozed_until and current_followup.snoozed_until > as_of
        )
    return cards


def update_followup(
    *, actor, submission_key, followup_id, snoozed_until=None, note="", request_id=""
):
    require_operator(actor)
    if snoozed_until and (timezone.is_naive(snoozed_until) or snoozed_until <= timezone.now()):
        raise BusinessError("稍后提醒时间必须包含时区且晚于现在。")

    def action():
        row = FollowUp.objects.select_for_update().get(pk=followup_id, resolved_at__isnull=True)
        row.snoozed_until = snoozed_until
        row.note = note.strip()
        row.last_actor = actor
        row.save()
        record_event(actor, "followup.updated", row, request_id)
        return {"kind": row.kind}

    return execute_once(
        f"followup.update:{actor.pk}",
        submission_key,
        dict(
            followup_id=str(followup_id),
            snoozed_until=snoozed_until.isoformat() if snoozed_until else None,
            note=note,
        ),
        action,
    )


def save_thresholds(*, actor, submission_key, request_id="", **values):
    require_admin(actor)
    normalized = {
        kind: int(values.get(kind.lower() + "_days", default))
        for kind, default in DEFAULT_THRESHOLDS.items()
        if kind != "K2"
    }

    def action():
        for kind, days in normalized.items():
            BottleneckThreshold.objects.update_or_create(kind=kind, defaults={"days": days})
        record_event(actor, "bottleneck.thresholds_saved", actor, request_id, **normalized)
        return {}

    return execute_once(f"thresholds.save:{actor.pk}", submission_key, normalized, action)
