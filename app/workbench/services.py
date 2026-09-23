import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from django.db import transaction
from django.utils import timezone

from app.accounts.policies import require_admin
from app.common.business import BusinessError, record_event

from .models import CostVersion, ExportBatch, ProductCost, Trade


def cost_at_payment(product, paid_at):
    if not paid_at or product.unit_fen is None:
        return None, None
    versions = product.revisions.all()
    version = (
        versions.filter(effective_at__lte=paid_at).order_by("-effective_at", "-version").first()
    )
    # First configuration also fills historical orders that had no configured cost.
    version = version or versions.first()
    if version:
        return version.unit_fen, version.version
    return product.unit_fen, product.version


def stamp(value):
    try:
        return (
            datetime.fromtimestamp(value, UTC)
            if type(value) is int and 0 < value < 10**11
            else None
        )
    except (ValueError, OSError, OverflowError):
        return None


def classify(data):
    """Use explicit facts; unresolved refund codes must never imply refund success."""
    status = data.get("order_status")
    refund = data.get("refund_status")
    paid = data.get("pay_amount")
    if type(paid) is not int or not 0 <= paid <= 10**12:
        return "REVIEW", "缺少有效实付金额"
    if status not in (11, 12, 21, 22, 23, 24) or type(status) is not int:
        return "REVIEW", "未知订单状态，请核对平台"
    if refund not in (0, 1, 2, 3, 4, 5, 6, 8) or isinstance(refund, bool):
        return "REVIEW", "未知退款状态，暂停发货并核对平台"
    if refund == 5 or status == 23 or stamp(data.get("refund_time")):
        if data.get("refund_amount") == paid and paid > 0:
            if stamp(data.get("refund_time")) and (refund == 5 or status == 23):
                return "REFUNDED", ""
        return "REVIEW", "退款金额与全额退款口径不一致，请核对平台"
    if data.get("refund_amount") not in (None, 0):
        return "REVIEW", "已有退款金额但缺少一致的退款成功事实，请核对平台"
    if status == 24 or stamp(data.get("cancel_time")):
        return "CLOSED", ""
    if refund in (1, 2, 3, 8):
        if data.get("apply_amount") not in (None, 0, paid):
            return "REVIEW", "退款申请金额不是整单金额，本版不处理部分退款"
        return "REFUNDING", "暂停发货和回款参考"
    if refund == 6:
        return "REVIEW", "退款已拒绝，请核对平台后续处理及发货责任"
    if not stamp(data.get("pay_time")):
        return ("UNPAID", "") if status == 11 else ("REVIEW", "缺少付款时间")
    if status == 12:
        if stamp(data.get("consign_time")) or stamp(data.get("confirm_time")):
            return "REVIEW", "未发货状态与发货/完成时间冲突"
        return "SHIPPING", ""
    if status == 22 and stamp(data.get("confirm_time")):
        return "COMPLETED", ""
    if status == 21:
        if stamp(data.get("confirm_time")):
            return "REVIEW", "已发货状态与完成时间冲突"
        return ("PENDING", "") if stamp(data.get("consign_time")) else ("SHIPPING", "")
    return "REVIEW", "订单状态与关键时间不一致"


def product_key(goods):
    return hashlib.sha256(
        json.dumps(
            [
                str(goods.get("product_id") or goods.get("item_id") or ""),
                str(goods.get("sku_id") or ""),
                goods.get("sku_text", ""),
            ],
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


def resolve_product(goods, shop):
    """Keep legacy keys, and match CSV's optional SKU through observed API identities."""
    goods = dict(goods)
    for key in ("product_id", "item_id", "sku_id", "sku_text"):
        goods[key] = str(goods.get(key) or "").strip()
    exact = ProductCost.objects.filter(shop=shop, key=product_key(goods)).first()
    if exact:
        return exact
    without_sku = {**goods, "sku_id": ""}
    if goods["sku_id"]:
        legacy_csv = ProductCost.objects.filter(shop=shop, key=product_key(without_sku)).first()
        if legacy_csv:
            return legacy_csv
    else:
        field = "product_id" if goods["product_id"] else "item_id"
        candidates = (
            Trade.objects.filter(
                shop=shop,
                platform__isnull=False,
                **{
                    f"platform__snapshot__goods__{field}": goods[field],
                    "platform__snapshot__goods__sku_text": goods["sku_text"],
                },
            )
            .exclude(product=None)
            .values_list("product_id", flat=True)
            .distinct()
        )
        found = list(ProductCost.objects.filter(shop=shop, pk__in=candidates))
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            raise BusinessError(
                "同一商品和规格对应多个平台规格标识，请在 CSV 的可选“规格标识”列填写 SKU ID。"
            )
    product, _ = ProductCost.objects.get_or_create(
        shop=shop,
        key=product_key(goods),
        defaults={"title": goods.get("title") or "未命名商品", "spec": goods["sku_text"]},
    )
    return product


def effective_order_data(row):
    data = dict(row.snapshot)
    # A proven full refund is terminal in this MVP. A stale aftercare summary cannot undo it.
    if classify(data)[0] == "REFUNDED":
        return data
    refund = row.refund_snapshot
    observed = row.refund_checked_at
    updated = stamp(refund.get("update_time")) if refund else None
    source_time = stamp(row.source_updated)
    if refund and (updated or observed) and source_time and (updated or observed) >= source_time:
        for key in ("refund_status", "refund_amount", "refund_time", "apply_amount"):
            if key in refund:
                data[key] = refund[key]
    return data


def batch_signature(trade):
    return hashlib.sha256(
        json.dumps(
            [
                trade.status,
                trade.title,
                trade.spec,
                trade.quantity,
                trade.receiver,
                trade.phone,
                trade.address,
                trade.shipping_note,
                trade.supplier,
                trade.unit_cost_fen,
                str(trade.recovered_at),
                trade.refund_waybill,
                trade.refund_note,
                trade.paid_fen,
                str(trade.refunded_at),
                str(trade.refund_applied_at),
                trade.refund_type,
                trade.refunded_fen,
            ],
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


def is_carryover(row, data):
    """Track pre-start obligations and post-start settlements without importing old history."""
    start = row.connection.sync_start_at
    if row.scope_status != "HISTORICAL" or not start or not stamp(data.get("pay_time")):
        return False
    status, _ = classify(data)
    if status in ("SHIPPING", "PENDING", "REFUNDING"):
        return True
    field = {"COMPLETED": "confirm_time", "REFUNDED": "refund_time"}.get(status)
    ended = stamp(data.get(field)) if field else None
    month_start = timezone.localtime(start).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    return bool(ended and ended >= month_start)


@transaction.atomic
def project(row, raw=None, *, include_history=False):
    data = effective_order_data(row)
    carryover = is_carryover(row, data)
    if (
        row.scope_status != "IN_SCOPE"
        and not carryover
        and not (include_history and row.scope_status == "HISTORICAL")
        and not Trade.objects.filter(
            shop=row.connection.shop, number=row.external_order_no
        ).exists()
    ):
        return None
    raw = raw or {}
    goods = data.get("goods", {})
    trade, created = Trade.objects.select_for_update().get_or_create(
        shop=row.connection.shop, number=row.external_order_no
    )
    previous_status = trade.status
    if created and row.scope_status == "HISTORICAL":
        trade.source = "API_HISTORY" if include_history else "API_CARRY"
    trade.platform = row
    trade.status, trade.issue = classify(data)
    if (
        trade.refund_success_confirmed_at
        and trade.status == "REVIEW"
        and data.get("order_status") == 23
        and stamp(data.get("refund_time"))
        and data.get("refund_amount") in (None, 0)
        and data.get("refund_status") in (0, 5)
    ):
        trade.status, trade.issue = "REFUNDED", ""
    if any("同一更新时间" in s for s in row.contract_issues):
        trade.status, trade.issue = "REVIEW", "平台同一版本内容冲突"
    if row.refund_snapshot.get("_needs_review"):
        trade.status, trade.issue = "REVIEW", "售后字段或版本冲突，请核对平台"
    quantity = goods.get("quantity")
    if type(quantity) is not int or not 1 <= quantity <= 1000000:
        trade.status, trade.issue = "REVIEW", "缺少有效商品数量"
    else:
        trade.quantity = quantity
    if goods.get("product_id") or goods.get("item_id"):
        product = resolve_product(goods, row.connection.shop)
        trade.product = product
        if trade.unit_cost_fen is None:
            trade.unit_cost_fen, trade.cost_version = cost_at_payment(
                product, stamp(data.get("pay_time"))
            )
        if not trade.supplier_override:
            trade.supplier = product.supplier
            trade.supplier_wechat = product.supplier_wechat
        if created or trade.status == "SHIPPING":
            trade.default_shipping_note = product.shipping_note
    trade.title, trade.spec = goods.get("title", ""), goods.get("sku_text", "")
    trade.paid_fen = data.get("pay_amount", 0)
    for field, key in (
        ("ordered_at", "order_time"),
        ("paid_at", "pay_time"),
        ("shipped_at", "consign_time"),
        ("completed_at", "confirm_time"),
        ("refunded_at", "refund_time"),
    ):
        setattr(trade, field, stamp(data.get(key)))
    for field, key in (
        ("receiver", "receiver_name"),
        ("phone", "receiver_mobile"),
        ("platform_note", "seller_remark"),
    ):
        if key in raw and (raw[key] or trade.status == "SHIPPING" or field == "platform_note"):
            value = str(raw.get(key) or "")
            limit = 1000 if field == "platform_note" else 100
            if len(value) > limit:
                setattr(trade, field, "")
                trade.status, trade.issue = "REVIEW", "收件信息或备注超过字段长度，请核对完整内容"
            else:
                setattr(trade, field, value)
    if "address" in raw and (raw["address"] or trade.status == "SHIPPING"):
        address = "".join(
            str(raw.get(k) or "")
            for k in ("prov_name", "city_name", "area_name", "town_name", "address")
        )
        if len(address) > 1000:
            trade.address = ""
            trade.status, trade.issue = "REVIEW", "地址超过字段长度，请核对完整地址"
        else:
            trade.address = address
    images = raw.get("goods", {}).get("images") or goods.get("images", [])
    if isinstance(images, list):
        for url in images:
            if isinstance(url, str) and urlsplit(url).scheme in ("http", "https"):
                trade.image = url[:1000]
                break
    trade.waybill = data.get("waybill_no", "")
    trade.synced_at = timezone.now()
    if row.refund_snapshot:
        trade.refund_waybill = row.refund_snapshot.get("waybill_no", trade.refund_waybill)
        trade.refund_applied_at = (
            stamp(row.refund_snapshot.get("apply_time")) or trade.refund_applied_at
        )
        refund_type = row.refund_snapshot.get("refund_type", trade.refund_type)
        trade.refund_type = refund_type if refund_type in (1, 2) else None
    if type(data.get("refund_amount")) is int:
        trade.refunded_fen = data["refund_amount"]
    if trade.refund_success_confirmed_at and trade.status == "REFUNDED" and not trade.refunded_fen:
        trade.refunded_fen = None
    if created or trade.status != previous_status:
        changed_at = stamp(data.get("update_time"))
        if trade.status in ("REFUNDING", "REFUNDED") and row.refund_snapshot:
            changed_at = stamp(row.refund_snapshot.get("update_time")) or row.refund_checked_at
        trade.status_changed_at = changed_at or timezone.now()
    trade.save()
    invalidate_batches(trade)
    return trade


@transaction.atomic
def confirm_refund_success(trade, actor):
    require_admin(actor)
    trade = (
        Trade.objects.select_for_update(of=("self",))
        .select_related("platform__connection")
        .get(pk=trade.pk)
    )
    if not trade.platform_id:
        raise BusinessError("此确认仅用于平台退款成功但缺少金额的订单。")
    data = effective_order_data(trade.platform)
    if not (
        data.get("order_status") == 23
        and stamp(data.get("refund_time"))
        and data.get("refund_amount") in (None, 0)
        and data.get("refund_status") in (0, 5)
    ):
        raise BusinessError("订单不符合退款成功但缺少金额的确认条件。")
    if not trade.refund_success_confirmed_at:
        trade.refund_success_confirmed_at = timezone.now()
        trade.save(update_fields=["refund_success_confirmed_at"])
        record_event(
            actor,
            "workbench.refund_success_confirmed",
            trade,
            source="user_confirmation",
            platform_refund_amount=data.get("refund_amount"),
        )
    return project(trade.platform)


@transaction.atomic
def import_saved_history(connection, actor):
    """Explicitly import missing saved history; never rewrite existing business records."""
    from app.integrations.models import Connection, PlatformOrder

    require_admin(actor)
    connection = Connection.objects.select_for_update().get(pk=connection.pk)
    rows = (
        PlatformOrder.objects.select_related("connection__shop")
        .filter(connection=connection, scope_status=PlatformOrder.Scope.HISTORICAL)
        .exclude(external_order_no__in=Trade.objects.filter(shop=connection.shop).values("number"))
    )
    imported = []
    for row in rows:
        trade = project(row, include_history=True)
        if trade is not None:
            imported.append(trade)
    if imported:
        record_event(
            actor,
            "workbench.saved_history_imported",
            connection,
            count=len(imported),
            review_count=sum(t.status == "REVIEW" for t in imported),
            trade_ids=[str(t.pk) for t in imported],
        )
    return imported


def supplier_choices(shop):
    """Reuse saved names within this shop; do not guess conflicting contact aliases."""
    contacts: dict[str, set[str]] = {}
    for model in (ProductCost, Trade):
        for name, wechat in (
            model.objects.filter(shop=shop)
            .exclude(supplier="")
            .values_list("supplier", "supplier_wechat")
            .distinct()
        ):
            name, wechat = name.strip(), wechat.strip()
            if name:
                aliases = contacts.setdefault(name, set())
                if wechat:
                    aliases.add(wechat)
    return [
        {"name": name, "wechat": next(iter(aliases)) if len(aliases) == 1 else ""}
        for name, aliases in sorted(contacts.items())
    ]


def invalidate_batches(trade):
    for batch in ExportBatch.objects.filter(shop=trade.shop, stale=False):
        if any(
            r["id"] == str(trade.pk) and r["signature"] != batch_signature(trade)
            for r in batch.snapshot
        ):
            batch.stale = True
            batch.save(update_fields=["stale"])


@transaction.atomic
def update_product(
    product, unit_fen, supplier, actor, *, supplier_wechat=None, shipping_note=None, condition=None
):
    product = ProductCost.objects.select_for_update().get(pk=product.pk)
    if not product.shop_id:
        raise BusinessError("商品尚未确定店铺归属，请先核对历史数据。")
    if product.unit_fen is not None:
        CostVersion.objects.get_or_create(
            product=product,
            version=product.version,
            defaults={"unit_fen": product.unit_fen, "effective_at": product.updated_at},
        )
    cost_changed = product.unit_fen != unit_fen
    product.unit_fen, product.supplier = unit_fen, supplier
    if supplier_wechat is not None:
        product.supplier_wechat = supplier_wechat
    if shipping_note is not None:
        product.shipping_note = shipping_note
    if condition is not None:
        product.condition = condition
    if cost_changed:
        product.version += 1
    product.save()
    if cost_changed:
        CostVersion.objects.create(
            product=product,
            version=product.version,
            unit_fen=unit_fen,
            effective_at=product.updated_at,
        )
    for trade in Trade.objects.select_for_update().filter(product=product, shop_id=product.shop_id):
        if trade.unit_cost_fen is None:
            cost_date = trade.paid_at or (
                trade.ordered_at if trade.source == "BILL_IMPORT" else None
            )
            trade.unit_cost_fen, trade.cost_version = cost_at_payment(product, cost_date)
        if not trade.supplier_override:
            trade.supplier = supplier
            trade.supplier_wechat = product.supplier_wechat
        if trade.status == "SHIPPING":
            trade.default_shipping_note = product.shipping_note
        trade.save()
        invalidate_batches(trade)
    record_event(
        actor,
        "workbench.cost_changed" if cost_changed else "workbench.product_changed",
        product,
        unit_fen=unit_fen,
        version=product.version,
    )


@transaction.atomic
def create_batches(trades, kind, actor):
    if kind not in ("shipping", "refund"):
        raise BusinessError("未知清单类型。")
    # Re-read and lock rows after API refresh. Caller objects may already be stale.
    trades = list(
        Trade.objects.select_for_update().filter(pk__in=[t.pk for t in trades]).order_by("pk")
    )
    if len({t.shop_id for t in trades}) > 1:
        raise BusinessError("一次只能导出同一店铺的订单。")
    groups: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        if kind == "shipping":
            if trade.status != "SHIPPING" or not trade.paid_at:
                continue
            if (
                not all((trade.spec, trade.receiver, trade.phone, trade.address, trade.supplier))
                or "*" in trade.phone + trade.address
            ):
                raise BusinessError("请先补齐型号规格、供应商和完整收件信息，再导出。")
        elif trade.status not in ("REFUNDING", "REFUNDED") or trade.recovered_at:
            continue
        if not trade.supplier:
            raise BusinessError("请先为订单选择供应商。")
        groups.setdefault(trade.supplier, []).append(
            {
                "id": str(trade.pk),
                "number": trade.number,
                "signature": batch_signature(trade),
                "title": trade.title,
                "condition": trade.display_condition,
                "spec": trade.spec,
                "quantity": trade.quantity,
                "receiver": trade.receiver,
                "phone": trade.phone,
                "address": trade.address,
                "note": trade.shipping_note,
                "cost_fen": trade.cost_fen,
                "paid_fen": trade.paid_fen,
                "status": trade.get_status_display(),
                "waybill": trade.refund_waybill,
                "refund_note": trade.refund_note,
                "refund_amount_fen": trade.refunded_fen
                if trade.status == "REFUNDED"
                else trade.paid_fen,
                "refund_applied_at": timezone.localtime(trade.refund_applied_at).strftime(
                    "%Y-%m-%d %H:%M"
                )
                if trade.refund_applied_at
                else "待核对",
                "refund_wait_days": max(0, (timezone.now() - trade.refund_applied_at).days)
                if trade.refund_applied_at
                else None,
                "return_required": trade.return_required,
            }
        )
    if not groups:
        raise BusinessError("同步后所选订单已不符合导出条件，请刷新列表。")
    return [
        ExportBatch.objects.create(
            shop=trades[0].shop, kind=kind, supplier=supplier, snapshot=rows, actor=actor
        )
        for supplier, rows in groups.items()
    ]
