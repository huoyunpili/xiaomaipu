import hashlib
import json
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import uuid4, uuid5
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from app.accounts.policies import require_admin, require_operator
from app.common.business import BusinessError, record_event
from app.orders.models import SalesOrder
from app.orders.services import create_order
from app.shops.models import SalesChannel, Shop

from .client import APIError, XgjClient, rows
from .models import (
    Connection,
    ExternalFactApplication,
    PlatformOrder,
    PlatformRefund,
    PlatformRefundRevision,
    PlatformRevision,
    SyncRun,
)

BEIJING = ZoneInfo("Asia/Shanghai")
LEASE_SECONDS = 180
OVERLAP_SECONDS = 600
SEMANTIC_VERSION = 1

# Only business metadata needed for reconciliation is persisted; no address/payment ID.
NUMBER_FIELDS = (
    "order_status",
    "order_time",
    "create_time",
    "update_time",
    "total_amount",
    "pay_amount",
    "pay_time",
    "refund_status",
    "refund_amount",
    "refund_time",
    "consign_time",
    "consign_type",
    "confirm_time",
    "cancel_time",
)
TEXT_FIELDS = (
    "buyer_nick",
    "waybill_no",
    "express_code",
    "express_name",
    "cancel_reason",
)
TRACKED_WITHDRAWALS = ("waybill_no", "express_code", "express_name", "consign_time")


def _digest(payload):
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _as_datetime(value):
    if type(value) is not int or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _day_start(day):
    return datetime.combine(day, time.min, tzinfo=BEIJING).astimezone(UTC)


def normalize(data):
    external = data.get("order_no")
    if not isinstance(external, str) or not external.isdigit() or len(external) > 100:
        raise APIError("平台订单号格式异常。")
    if type(data.get("update_time")) is not int or data["update_time"] <= 0:
        raise APIError("平台订单缺少有效更新时间。")
    clean: dict[str, Any] = {"order_no": external}
    for key in NUMBER_FIELDS:
        value = data.get(key)
        if value is not None:
            if type(value) is not int or not 0 <= value <= 10**12:
                raise APIError("平台订单数字字段格式异常。")
            clean[key] = value
    for key in TEXT_FIELDS:
        value = data.get(key)
        if isinstance(value, str) and value:
            clean[key] = value[:200]
    goods = data.get("goods")
    if isinstance(goods, dict):
        clean["goods"] = {}
        for key in (
            "product_id",
            "item_id",
            "sku_id",
            "outer_id",
            "sku_outer_id",
            "title",
            "sku_text",
        ):
            value = goods.get(key)
            if key == "sku_text" and isinstance(value, str) and len(value) > 200:
                raise APIError("平台型号规格超过当前字段长度，请先核对完整规格；未截断保存。")
            if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value):
                clean["goods"][key] = str(value)[:200]
        for key in ("quantity", "price"):
            value = goods.get(key)
            if type(value) is int and 0 <= value <= 10**12:
                clean["goods"][key] = value
        images = goods.get("images")
        if isinstance(images, list):
            valid_images = [
                url
                for url in images
                if isinstance(url, str)
                and url.startswith(("https://", "http://"))
                and len(url) <= 1000
            ][:20]
            if valid_images:
                clean["goods"]["images"] = valid_images
    return clean


def _scope(connection, snapshot):
    created = _as_datetime(snapshot.get("order_time"))
    if not connection.sync_start_at:
        return (
            PlatformOrder.Scope.UNKNOWN,
            None,
            "本实例尚未确认自动同步起始日。",
        )
    if not created:
        return (
            PlatformOrder.Scope.UNKNOWN,
            None,
            "平台订单缺少有效创建时间，未自动纳入经营范围。",
        )
    if created < connection.sync_start_at:
        return (
            PlatformOrder.Scope.HISTORICAL,
            created,
            "订单创建于接入起始日之前，继续使用历史导入核对。",
        )
    return PlatformOrder.Scope.IN_SCOPE, created, ""


def _order_issues(data, clean, previous):
    issues = []
    if not _as_datetime(clean.get("order_time") or previous.get("order_time")):
        issues.append("缺少有效订单创建时间")
    consign_time = _as_datetime(clean.get("consign_time"))
    consign_type = clean.get("consign_type")
    if consign_type is not None and consign_type not in (0, 1, 2):
        issues.append(f"未知发货方式代码 {consign_type}")
    if consign_time and consign_type not in (1, 2):
        issues.append("已有平台发货时间，但发货方式缺失或未知")
    if consign_type in (1, 2) and not consign_time:
        issues.append("已有平台发货方式，但缺少有效发货时间")
    withdrawn = [
        key
        for key in TRACKED_WITHDRAWALS
        if previous.get(key) not in (None, "", 0) and key in data and data.get(key) in (None, "", 0)
    ]
    if withdrawn:
        issues.append("平台撤回字段待核对：" + "、".join(withdrawn))
    if clean.get("cancel_time", 0) or clean.get("cancel_reason"):
        issues.append("平台出现取消信息，未自动取消本地订单")
    return issues


def _match_existing_order(row):
    if row.order_id:
        return row.order_id
    channel = SalesChannel.objects.filter(code="XIANYU", is_active=True).first()
    if not channel:
        return None
    order = SalesOrder.objects.filter(
        channel=channel, external_order_no=row.external_order_no
    ).first()
    if order:
        row.order = order
        row.auto_matched = True
    return row.order_id


def _save_application(
    *,
    connection,
    fact_type,
    external_key,
    source_updated,
    payload,
    result,
    target_type="",
    target_id="",
    reason="",
):
    ExternalFactApplication.objects.update_or_create(
        connection=connection,
        fact_type=fact_type,
        external_key=external_key,
        semantic_version=SEMANTIC_VERSION,
        defaults={
            "source_updated": source_updated,
            "payload_hash": _digest(payload),
            "result": result,
            "target_type": target_type,
            "target_id": str(target_id or ""),
            "reason": reason[:500],
        },
    )


@transaction.atomic
def store_order(connection, data):
    clean = normalize(data)
    row, created = PlatformOrder.objects.select_for_update().get_or_create(
        connection=connection,
        external_order_no=clean["order_no"],
        defaults={"source_updated": clean["update_time"], "snapshot": clean},
    )
    if not created and clean["update_time"] < row.source_updated:
        return row

    previous = dict(row.snapshot) if not created else {}
    issues = _order_issues(data, clean, previous)
    if not created and clean["update_time"] > row.source_updated:
        merged = dict(row.snapshot)
        merged.update(clean)
        if "goods" in clean:
            merged["goods"] = {**row.snapshot.get("goods", {}), **clean["goods"]}
        row.snapshot = merged
        row.source_updated = clean["update_time"]
    elif created:
        row.snapshot = clean
    else:
        candidate = {**row.snapshot, **clean}
        if "goods" in clean:
            candidate["goods"] = {**row.snapshot.get("goods", {}), **clean["goods"]}
        conflicts = any(
            key in row.snapshot and row.snapshot[key] != value
            for key, value in clean.items()
            if key != "goods"
        ) or any(
            key in row.snapshot.get("goods", {}) and row.snapshot["goods"][key] != value
            for key, value in clean.get("goods", {}).items()
            if key != "images"
        )
        if conflicts:
            issues.append("同一更新时间返回不同内容，保留原版本并等待人工核对")
        else:
            # Detail responses may add facts omitted by the list at the same version.
            row.snapshot = candidate

    scope_status, source_created_at, scope_reason = _scope(connection, row.snapshot)
    row.scope_status = scope_status
    row.source_created_at = source_created_at
    row.scope_reason = scope_reason
    row.contract_issues = issues
    _match_existing_order(row)
    if scope_status == PlatformOrder.Scope.HISTORICAL:
        row.needs_review = False
        result = ExternalFactApplication.Result.OUT_OF_SCOPE
        reason = scope_reason
    elif scope_status == PlatformOrder.Scope.UNKNOWN or issues:
        row.needs_review = True
        result = ExternalFactApplication.Result.NEEDS_REVIEW
        reason = "；".join([scope_reason, *issues]).strip("；")
    elif row.order_id:
        row.needs_review = True
        result = ExternalFactApplication.Result.LINKED
        reason = "仅匹配本地订单；未自动更改库存、履约或现金。"
    else:
        row.needs_review = True
        result = ExternalFactApplication.Result.STORED
        reason = "平台事实仅供核对；未自动更改库存、履约或现金。"
    row.save()

    PlatformRevision.objects.get_or_create(
        platform_order=row,
        source_updated=row.source_updated,
        defaults={"snapshot": row.snapshot},
    )
    _save_application(
        connection=connection,
        fact_type="ORDER",
        external_key=row.external_order_no,
        source_updated=row.source_updated,
        payload=row.snapshot,
        result=result,
        target_type="SalesOrder" if row.order_id else "",
        target_id=row.order_id,
        reason=reason,
    )
    from app.workbench.services import project

    project(row, data if not any("同一更新时间" in v for v in issues) else {})
    return row


def connect(*, actor, client=None):
    require_admin(actor)
    stores = [s for s in rows((client or XgjClient()).call("stores")) if s.get("is_valid") is True]
    shop = Shop.objects.get(is_active=True)
    old = Connection.objects.filter(shop=shop).first()
    expected = old.seller_id if old else shop.external_shop_id
    if expected:
        stores = [s for s in stores if str(s.get("authorize_id")) == expected]
    if len(stores) != 1:
        raise APIError("无法唯一匹配有效授权店铺，请核对店铺授权及店铺设置。")
    seller = str(stores[0].get("authorize_id", ""))
    if not seller.isdigit():
        raise APIError("平台店铺标识格式异常。")
    now = timezone.now()
    with transaction.atomic():
        connection = Connection.objects.select_for_update().filter(shop=shop).first()
        if connection is None:
            connection = Connection(
                shop=shop,
                seller_id=seller,
                actor=actor,
                first_connected_at=now,
                sync_start_at=_day_start(now.astimezone(BEIJING).date()),
                sync_start_basis="FIRST_CONNECTION",
                sync_start_confirmed_at=now,
                sync_start_confirmed_by=actor,
            )
        else:
            connection.seller_id = seller
            connection.actor = actor
        connection.error = "" if connection.sync_start_at else "请先确认自动同步起始日。"
        connection.service_info = {
            key: stores[0].get(key)
            for key in ("is_trial", "is_pro", "valid_end_time", "service_support")
        }
        connection.save()
        record_event(actor, "xgj.connected", connection)
    return connection


@transaction.atomic
def confirm_sync_start(*, actor, connection, start_date: date):
    require_admin(actor)
    connection = Connection.objects.select_for_update().get(pk=connection.pk)
    proposed = _day_start(start_date)
    today_start = _day_start(timezone.now().astimezone(BEIJING).date())
    if proposed > today_start:
        raise BusinessError("自动同步起始日不能晚于今天。")
    if connection.sync_start_at and connection.sync_start_at != proposed:
        raise BusinessError("自动同步起始日已经固定，不能通过重连或重试修改。")
    connection.sync_start_at = proposed
    connection.first_connected_at = connection.first_connected_at or timezone.now()
    connection.sync_start_basis = connection.sync_start_basis or "ADMIN_CONFIRMED"
    connection.sync_start_confirmed_at = timezone.now()
    connection.sync_start_confirmed_by = actor
    connection.error = ""
    connection.save()
    record_event(actor, "xgj.sync_start_confirmed", connection, start_date=start_date.isoformat())
    return connection


@transaction.atomic
def queue_sync(connection, *, full=False, history_import=False):
    connection = Connection.objects.select_for_update().get(pk=connection.pk)
    if not connection.sync_start_at:
        raise APIError("请先确认自动同步起始日，再从闲管家同步历史订单。")
    now_dt = timezone.now()
    active = connection.runs.filter(status__in=["QUEUED", "RUNNING", "RETRY"]).first()
    if active:
        if history_import:
            raise APIError("当前已有同步任务，请等待完成后再导入闲管家历史订单。")
        if (
            active.status == "RUNNING"
            and active.lease_expires_at
            and active.lease_expires_at <= now_dt
        ):
            active.status = "RETRY"
            active.lease_owner = ""
            active.lease_expires_at = None
            active.error = "上次执行者心跳失效，任务等待安全接管。"
            active.save()
        return active
    now = int(now_dt.timestamp())
    # Monthly reports need the entire connection month, including earlier settlements.
    start = max(
        0,
        int(
            _day_start(
                connection.sync_start_at.astimezone(BEIJING).date().replace(day=1)
            ).timestamp()
        ),
    )
    if connection.sync_start_at > now_dt:
        raise APIError("自动同步起始日晚于当前时间，请核对电脑时间。")
    if not full and connection.cursor and now - connection.cursor > 179 * 86400:
        raise APIError("同步中断已超过平台查询时间范围，请先用表格补齐历史并重新核对。")
    if history_import:
        # The provider limits order-list queries to the most recent six months.
        # Stay one day inside that boundary to avoid clock/rounding rejection.
        cursor_start = max(0, now - 179 * 86400)
    else:
        cursor_start = (
            connection.cursor - OVERLAP_SECONDS if connection.cursor and not full else start
        )
    return SyncRun.objects.create(
        connection=connection,
        window_start=cursor_start if history_import else max(cursor_start, start),
        window_end=now,
        history_import_requested=history_import,
    )


def _claim_sync(run_id, owner):
    with transaction.atomic():
        run = SyncRun.objects.select_for_update().filter(pk=run_id).first()
        now = timezone.now()
        if not run or run.status in ("DONE", "FAILED"):
            return None
        if run.status == "RUNNING" and run.lease_expires_at and run.lease_expires_at > now:
            return None
        run.status = "RUNNING"
        run.attempts += 1
        run.lease_owner = owner
        run.lease_generation += 1
        run.heartbeat_at = now
        run.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        run.save()
        return run.lease_generation


def _owns(run, owner, generation):
    return (
        run.status == "RUNNING" and run.lease_owner == owner and run.lease_generation == generation
    )


def _apply_page(run_id, owner, generation, page, batch):
    with transaction.atomic():
        run = SyncRun.objects.select_for_update().select_related("connection").get(pk=run_id)
        if not _owns(run, owner, generation) or run.page != page:
            return None
        if len(batch) == 100 and run.page >= 100:
            raise APIError(
                "达到闲管家单次 10000 条查询上限，本次未完整导入；请先在闲管家缩小或分批整理历史订单后重试。"
            )
        for data in batch:
            if not (
                type(data.get("update_time")) is int
                and run.window_start <= data["update_time"] <= run.window_end
            ):
                raise APIError("平台返回订单不在请求的更新时间范围，未推进同步进度。")
            normalize(data)
        status_counts = {
            PlatformOrder.Scope.IN_SCOPE: 0,
            PlatformOrder.Scope.HISTORICAL: 0,
            PlatformOrder.Scope.UNKNOWN: 0,
        }
        for data in batch:
            row = store_order(run.connection, data)
            status_counts[row.scope_status] += 1
        run.count += len(batch)
        run.scanned_count += len(batch)
        run.accepted_count += status_counts[PlatformOrder.Scope.IN_SCOPE]
        run.historical_count += status_counts[PlatformOrder.Scope.HISTORICAL]
        run.review_count += status_counts[PlatformOrder.Scope.UNKNOWN]
        now = timezone.now()
        run.heartbeat_at = now
        run.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        if len(batch) < 100:
            run.status = "DONE"
            run.finished_at = now
            run.lease_owner = ""
            run.lease_expires_at = None
            Connection.objects.filter(pk=run.connection_id).update(
                last_success=now, cursor=run.window_end, error=""
            )
        else:
            run.page += 1
        run.error = ""
        run.save()
        return run.status == "DONE"


def _fail_owned(run_id, owner, generation, message, retryable):
    with transaction.atomic():
        run = SyncRun.objects.select_for_update().filter(pk=run_id).first()
        if not run or not _owns(run, owner, generation):
            return False
        retry = retryable and run.attempts < 3
        run.status = "RETRY" if retry else "FAILED"
        run.error = message[:300]
        run.lease_owner = ""
        run.lease_expires_at = None
        run.save()
        Connection.objects.filter(pk=run.connection_id).update(error=message[:300])
        return True


def execute_sync(run_id, client=None, worker_id=None):
    client = client or XgjClient()
    owner = worker_id or uuid4().hex
    generation = _claim_sync(run_id, owner)
    if generation is None:
        return
    try:
        while True:
            run = SyncRun.objects.select_related("connection").get(pk=run_id)
            if not _owns(run, owner, generation):
                return
            page = run.page
            params: dict[str, Any] = {
                "page_size": 100,
                "page_no": page,
                "update_time": [run.window_start, run.window_end],
            }
            batch = rows(client.call("orders", params, seller=run.connection.seller_id))
            finished = _apply_page(run_id, owner, generation, page, batch)
            if finished is None:
                return
            if finished:
                completed = SyncRun.objects.select_related("connection__actor").get(pk=run_id)
                if completed.history_import_requested:
                    from app.workbench.services import import_saved_history

                    try:
                        imported = import_saved_history(
                            completed.connection, completed.connection.actor
                        )
                    except Exception:
                        message = "闲管家订单已同步，但历史订单写入失败，请重试历史导入。"
                        SyncRun.objects.filter(pk=completed.pk).update(
                            status="FAILED", error=message
                        )
                        Connection.objects.filter(pk=completed.connection_id).update(error=message)
                        return
                    else:
                        SyncRun.objects.filter(pk=completed.pk).update(
                            history_imported_count=len(imported)
                        )
                return
    except APIError as exc:
        if _fail_owned(run_id, owner, generation, str(exc), exc.retryable):
            raise
    except Exception:
        _fail_owned(
            run_id,
            owner,
            generation,
            "同步意外中断，请重试；已保存的平台事实会自动去重。",
            False,
        )
        raise


@transaction.atomic
def convert_order(*, actor, row_id, sku_id, quantity, unit_price_fen, selected_lot_id=None):
    require_operator(actor)
    row = PlatformOrder.objects.select_for_update().get(pk=row_id)
    if row.order_id:
        return row.order
    if row.scope_status == PlatformOrder.Scope.HISTORICAL:
        raise BusinessError("接入日前订单请使用闲管家历史订单同步，避免作为新单重复履约。")
    channel = SalesChannel.objects.get(code="XIANYU", is_active=True)
    existing = SalesOrder.objects.filter(
        channel=channel, external_order_no=row.external_order_no
    ).first()
    if existing:
        row.order = existing
        row.auto_matched = True
    else:
        result = create_order(
            actor=actor,
            submission_key=uuid5(row.pk, "convert"),
            sku_id=sku_id,
            quantity=quantity,
            unit_price_fen=unit_price_fen,
            selected_lot_id=selected_lot_id,
            channel_id=channel.pk,
            customer_name=row.snapshot.get("buyer_nick", "闲鱼买家")[:100],
            external_order_no=row.external_order_no,
        )
        row.order_id = result["order_id"]
    row.needs_review = False
    row.save()
    _save_application(
        connection=row.connection,
        fact_type="ORDER",
        external_key=row.external_order_no,
        source_updated=row.source_updated,
        payload=row.snapshot,
        result=ExternalFactApplication.Result.LINKED,
        target_type="SalesOrder",
        target_id=row.order_id,
        reason="操作员确认后匹配销售草稿；平台事实未生成现金或履约动作。",
    )
    record_event(actor, "xgj.order_linked", row)
    return row.order


def refresh_order(row, client=None):
    data = (client or XgjClient()).call(
        "detail", {"order_no": row.external_order_no}, seller=row.connection.seller_id
    )
    if data.get("order_no") != row.external_order_no:
        raise BusinessError("平台返回订单与查询订单不一致。")
    return store_order(row.connection, data)


REFUND_NUMBER_FIELDS = (
    "goods_status",
    "refund_type",
    "refund_status",
    "refund_amount",
    "apply_amount",
    "refund_time",
    "apply_time",
    "update_time",
    "timeout_time",
    "timeout_status",
    "timeout_type",
    "reject_time",
)
REFUND_TEXT_FIELDS = (
    "refund_no",
    "waybill_no",
    "express_code",
    "express_name",
    "refund_reason",
)


def normalize_refund(data):
    order_no = data.get("order_no")
    refund_no = data.get("refund_no")
    if not isinstance(order_no, str) or not order_no.isdigit() or len(order_no) > 100:
        raise APIError("平台售后缺少有效订单号。")
    if not isinstance(refund_no, str) or not refund_no.strip() or len(refund_no) > 120:
        raise APIError("平台售后缺少独立售后编号。")
    updated = data.get("update_time")
    if type(updated) is not int or updated <= 0:
        raise APIError("平台售后缺少有效更新时间。")
    clean: dict[str, Any] = {"order_no": order_no, "refund_no": refund_no[:120]}
    for key in REFUND_NUMBER_FIELDS:
        value = data.get(key)
        if key == "timeout_type" and isinstance(value, str) and value.strip().isdigit():
            value = int(value.strip())
        if value is not None:
            if type(value) is not int or not 0 <= value <= 10**12:
                raise APIError("平台售后数字字段格式异常。")
            clean[key] = value
    for key in REFUND_TEXT_FIELDS:
        value = data.get(key)
        if isinstance(value, str) and value:
            clean[key] = value[:300]
    return clean


@transaction.atomic
def store_refund(connection, data):
    clean = normalize_refund(data)
    order = (
        PlatformOrder.objects.select_for_update()
        .filter(connection=connection, external_order_no=clean["order_no"])
        .first()
    )
    if not order:
        raise APIError("售后对应的平台订单尚未保存，已停止自动匹配。")
    refund, created = PlatformRefund.objects.select_for_update().get_or_create(
        connection=connection,
        external_refund_no=clean["refund_no"],
        defaults={
            "platform_order": order,
            "external_order_no": clean["order_no"],
            "source_updated": clean["update_time"],
            "snapshot": clean,
        },
    )
    if refund.external_order_no != clean["order_no"]:
        raise APIError("同一售后编号对应了不同订单，已停止自动匹配。")
    if not created and clean["update_time"] < refund.source_updated:
        return refund
    same_version_conflict = False
    if not created and clean["update_time"] > refund.source_updated:
        merged = dict(refund.snapshot)
        merged.update(clean)
        refund.snapshot = merged
        refund.source_updated = clean["update_time"]
    elif not created:
        same_version_conflict = {**refund.snapshot, **clean} != refund.snapshot
    issues = []
    if same_version_conflict:
        issues.append("同一售后更新时间返回不同内容，保留原版本并等待人工核对")
    if clean.get("refund_type") not in (None, 1, 2):
        issues.append(f"未知售后类型代码 {clean['refund_type']}")
    refund.contract_issues = issues
    refund.needs_review = True
    refund.save()
    PlatformRefundRevision.objects.get_or_create(
        platform_refund=refund,
        source_updated=refund.source_updated,
        defaults={"snapshot": refund.snapshot},
    )
    _save_application(
        connection=connection,
        fact_type="REFUND",
        external_key=refund.external_refund_no,
        source_updated=refund.source_updated,
        payload=refund.snapshot,
        result=ExternalFactApplication.Result.NEEDS_REVIEW,
        target_type="SalesOrder" if order.order_id else "",
        target_id=order.order_id,
        reason="；".join(issues) or "独立售后事实仅供核对；未自动退款、收货或改变库存。",
    )
    if refund.source_updated >= order.refund_snapshot.get("update_time", 0):
        order.refund_snapshot = {**refund.snapshot, "_needs_review": bool(issues)}
        order.refund_checked_at = timezone.now()
        order.save(update_fields=["refund_snapshot", "refund_checked_at"])
        from app.workbench.services import project

        project(order)
    return refund


@transaction.atomic
def refresh_refund(*, actor, row_id, client=None):
    require_operator(actor)
    row = PlatformOrder.objects.select_for_update().get(pk=row_id)
    data = (client or XgjClient()).call(
        "refund_detail", {"order_no": row.external_order_no}, seller=row.connection.seller_id
    )
    if data.get("order_no") != row.external_order_no:
        raise APIError("未获得该订单的有效售后详情，请在平台核对。")
    clean: dict[str, Any] = {}
    for key in REFUND_NUMBER_FIELDS:
        value = data.get(key)
        if key == "timeout_type" and isinstance(value, str) and value.strip().isdigit():
            value = int(value.strip())
        if value is not None:
            if type(value) is not int or not 0 <= value <= 10**12:
                raise APIError("平台售后数字字段格式异常。")
            clean[key] = value
    for key in REFUND_TEXT_FIELDS:
        value = data.get(key)
        if isinstance(value, str) and value:
            clean[key] = value[:300]
    if data.get("refund_no") and data.get("update_time"):
        store_refund(row.connection, data)
        row.refresh_from_db()
    else:
        # Detail responses have no documented update_time. Treat the response as an
        # observation now, without inventing an independent platform version.
        row.refund_snapshot = clean
        row.refund_checked_at = timezone.now()
        row.needs_review = True
        row.save()
        _save_application(
            connection=row.connection,
            fact_type="REFUND_SUMMARY",
            external_key=row.external_order_no,
            source_updated=row.source_updated,
            payload=row.refund_snapshot,
            result=ExternalFactApplication.Result.NEEDS_REVIEW,
            target_type="SalesOrder" if row.order_id else "",
            target_id=row.order_id,
            reason="售后详情缺少独立售后编号或更新时间，未自动建立售后业务单。",
        )
    record_event(actor, "xgj.refund_checked", row)
    from app.workbench.services import project

    project(row)
    return row
