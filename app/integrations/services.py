from typing import Any
from uuid import uuid5

from django.db import transaction
from django.utils import timezone

from app.accounts.policies import require_admin, require_operator
from app.common.business import BusinessError, record_event
from app.orders.models import SalesOrder
from app.orders.services import create_order
from app.shops.models import SalesChannel, Shop

from .client import APIError, XgjClient, rows
from .models import Connection, PlatformOrder, PlatformRevision, SyncRun

# Only business metadata needed for reconciliation is persisted; no address/payment ID.
NUMBER_FIELDS = (
    "order_status",
    "order_time",
    "update_time",
    "total_amount",
    "pay_amount",
    "pay_time",
    "refund_status",
    "refund_amount",
    "refund_time",
    "confirm_time",
)
TEXT_FIELDS = ("buyer_nick", "waybill_no", "express_name")


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
            if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value):
                clean["goods"][key] = str(value)[:200]
        for key in ("quantity", "price"):
            value = goods.get(key)
            if type(value) is int and 0 <= value <= 10**12:
                clean["goods"][key] = value
    return clean


@transaction.atomic
def store_order(connection, data):
    clean = normalize(data)
    row, created = PlatformOrder.objects.select_for_update().get_or_create(
        connection=connection,
        external_order_no=clean["order_no"],
        defaults={"source_updated": clean["update_time"], "snapshot": clean},
    )
    if not created and clean["update_time"] > row.source_updated:
        merged = dict(row.snapshot)
        merged.update(clean)
        if "goods" in clean:
            merged["goods"] = {**row.snapshot.get("goods", {}), **clean["goods"]}
        row.snapshot = merged
        row.source_updated = clean["update_time"]
        row.needs_review = True
        row.save()
    if created or clean["update_time"] == row.source_updated:
        PlatformRevision.objects.get_or_create(
            platform_order=row,
            source_updated=row.source_updated,
            defaults={"snapshot": row.snapshot},
        )
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
    with transaction.atomic():
        connection, _ = Connection.objects.update_or_create(
            shop=shop,
            defaults={
                "seller_id": seller,
                "actor": actor,
                "error": "",
                "service_info": {
                    key: stores[0].get(key)
                    for key in ("is_trial", "is_pro", "valid_end_time", "service_support")
                },
            },
        )
        record_event(actor, "xgj.connected", connection)
    return connection


@transaction.atomic
def queue_sync(connection, *, full=False):
    connection = Connection.objects.select_for_update().get(pk=connection.pk)
    active = connection.runs.filter(status__in=["QUEUED", "RUNNING", "RETRY"]).first()
    if active:
        return active
    now = int(timezone.now().timestamp())
    if not full and connection.cursor and now - connection.cursor > 179 * 86400:
        raise APIError("同步中断已超过平台查询时间范围，请先用表格补齐历史并重新核对。")
    return SyncRun.objects.create(
        connection=connection,
        window_start=0 if full else max(connection.cursor - 600, 0),
        window_end=now,
    )


def execute_sync(run_id, client=None):
    client = client or XgjClient()
    # A database lock prevents duplicate workers from processing the same page/run.
    with transaction.atomic():
        run = SyncRun.objects.select_for_update(skip_locked=True).filter(pk=run_id).first()
        if not run or run.status in ("DONE", "FAILED"):
            return
        run.status = "RUNNING"
        run.attempts += 1
        run.save()
    try:
        while True:
            with transaction.atomic():
                run = SyncRun.objects.select_for_update(skip_locked=True).filter(pk=run_id).first()
                if not run or run.status != "RUNNING":
                    return
                params: dict[str, Any] = {"page_size": 100, "page_no": run.page}
                if run.window_start:
                    params["update_time"] = [run.window_start, run.window_end]
                batch = rows(client.call("orders", params, seller=run.connection.seller_id))
                for data in batch:
                    if run.window_start and not (
                        type(data.get("update_time")) is int
                        and run.window_start <= data["update_time"] <= run.window_end
                    ):
                        raise APIError("平台返回订单不在请求的更新时间范围，未推进同步进度。")
                    store_order(run.connection, data)
                run.count += len(batch)
                if len(batch) < 100:
                    run.status = "DONE"
                    run.finished_at = timezone.now()
                    Connection.objects.filter(pk=run.connection_id).update(
                        last_success=run.finished_at, cursor=run.window_end, error=""
                    )
                elif run.page >= 100:
                    raise APIError("达到平台 10000 条查询上限，历史覆盖不完整，请补充表格导入。")
                else:
                    run.page += 1
                run.error = ""
                run.save()
                if run.status == "DONE":
                    return
    except APIError as exc:
        run = SyncRun.objects.get(pk=run_id)
        retry = exc.retryable and run.attempts < 3
        SyncRun.objects.filter(pk=run_id).update(
            status="RETRY" if retry else "FAILED", error=str(exc)
        )
        Connection.objects.filter(pk=run.connection_id).update(error=str(exc))
        raise


@transaction.atomic
def convert_order(*, actor, row_id, sku_id, quantity, unit_price_fen, selected_lot_id=None):
    require_operator(actor)
    row = PlatformOrder.objects.select_for_update().get(pk=row_id)
    if row.order_id:
        return row.order
    channel = SalesChannel.objects.get(code="XIANYU", is_active=True)
    existing = SalesOrder.objects.filter(
        channel=channel, external_order_no=row.external_order_no
    ).first()
    if existing:
        row.order = existing
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
    record_event(actor, "xgj.order_linked", row)
    return row.order


def refresh_order(row, client=None):
    data = (client or XgjClient()).call(
        "detail", {"order_no": row.external_order_no}, seller=row.connection.seller_id
    )
    if data.get("order_no") != row.external_order_no:
        raise BusinessError("平台返回订单与查询订单不一致。")
    return store_order(row.connection, data)


@transaction.atomic
def refresh_refund(*, actor, row_id, client=None):
    require_operator(actor)
    row = PlatformOrder.objects.select_for_update().get(pk=row_id)
    data = (client or XgjClient()).call(
        "refund_detail", {"order_no": row.external_order_no}, seller=row.connection.seller_id
    )
    if data.get("order_no") != row.external_order_no:
        raise APIError("未获得该订单的有效售后详情，请在平台核对。")
    clean = {}
    for key in (
        "goods_status",
        "refund_type",
        "refund_status",
        "refund_amount",
        "apply_amount",
        "refund_time",
        "apply_time",
        "timeout_time",
        "timeout_status",
        "timeout_type",
    ):
        value = data.get(key)
        if value is not None:
            if type(value) is not int or not 0 <= value <= 10**12:
                raise APIError("平台售后字段格式异常。")
            clean[key] = value
    row.refund_snapshot = {**row.refund_snapshot, **clean}
    row.refund_checked_at = timezone.now()
    row.needs_review = True
    row.save()
    record_event(actor, "xgj.refund_checked", row)
    return row
