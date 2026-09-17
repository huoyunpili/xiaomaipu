"""Scoped supplier uploads and durable, at-most-once automatic shipping attempts."""

import hashlib
import os
import re
import shutil
import uuid
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from app.common.business import BusinessError, record_event
from app.integrations.client import APIError, APIRejected, XgjClient, rows
from app.integrations.services import refresh_order

from .models import SupplierAccess, SupplierDispatch, SupplierVideo, Trade
from .services import effective_order_data

MAX_VIDEO = 60 * 1024 * 1024


def carriers(client=None):
    cached = cache.get("supplier_carriers")
    if cached is not None:
        return cached
    result = rows((client or XgjClient()).call("express"))
    result = [
        {"code": r["code"], "name": r["express_name"]}
        for r in result
        if isinstance(r.get("code"), str) and isinstance(r.get("express_name"), str)
    ]
    if not result:
        raise APIError("暂时无法取得快递公司列表，请稍后再试。")
    cache.set("supplier_carriers", result, 3600)
    return result


def valid_access(access):
    # Revalidate after slow uploads/API reads; another request may revoke access.
    access.refresh_from_db(fields=["revoked_at", "expires_at"])
    access.batch.shop.refresh_from_db(fields=["is_active"])
    if access.revoked_at or access.expires_at <= timezone.now() or not access.batch.shop.is_active:
        raise BusinessError("链接已失效，请联系店主重新提供。")


def scoped_trade(access, trade_id):
    valid_access(access)
    if str(trade_id) not in {r["id"] for r in access.batch.snapshot}:
        raise BusinessError("该订单不属于这份清单。")
    trade = (
        Trade.objects.select_related("platform__connection", "product")
        .filter(pk=trade_id, shop=access.batch.shop, supplier=access.batch.supplier)
        .first()
    )
    if not trade:
        raise BusinessError("订单已调整，请联系店主。")
    return trade


def check_shipping_snapshot(access, trade):
    original = next(r for r in access.batch.snapshot if r["id"] == str(trade.pk))
    for field in ("title", "spec", "quantity", "receiver", "phone", "address", "note"):
        value = trade.shipping_note if field == "note" else getattr(trade, field)
        if field not in original or original[field] != value:
            raise BusinessError("商品、地址或备注已有变更，请联系店主重新生成清单。")


def ensure_access(batch):
    if batch.kind != "shipping":
        raise BusinessError("只有发货清单可创建上传链接。")
    access, _ = SupplierAccess.objects.get_or_create(
        batch=batch, defaults={"expires_at": timezone.now() + timedelta(days=30)}
    )
    return access


def video_path(name):
    root = (Path(settings.PRIVATE_MEDIA_ROOT) / "supplier-evidence").resolve()
    target = (Path(settings.PRIVATE_MEDIA_ROOT) / name).resolve()
    if not target.is_relative_to(root):
        raise BusinessError("视频位置无效。")
    return target


def save_video(access, trade_id, upload):
    scoped_trade(access, trade_id)
    if not upload or not 0 < upload.size <= MAX_VIDEO:
        raise BusinessError("请选择不超过 60MB 的视频。")
    header = upload.read(32)
    upload.seek(0)
    if header[4:8] == b"ftyp":
        extension, mime = (
            (".mov", "video/quicktime") if header[8:12] == b"qt  " else (".mp4", "video/mp4")
        )
    elif header[:4] == b"\x1aE\xdf\xa3":
        extension, mime = ".webm", "video/webm"
    else:
        raise BusinessError("仅支持 MP4、MOV、WebM 视频，请勿修改文件后缀冒充视频。")
    name = f"supplier-evidence/{trade_id}/{uuid.uuid4().hex}{extension}"
    target = video_path(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(target.parent).free < upload.size + 1024**3:
        raise BusinessError("店主电脑剩余空间不足，请联系店主。")
    digest, size = hashlib.sha256(), 0
    try:
        with target.open("xb") as output:
            for chunk in upload.chunks():
                size += len(chunk)
                if size > MAX_VIDEO:
                    raise BusinessError("视频超过 60MB。")
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        with target.open("rb") as stored:
            if (
                size != upload.size
                or hashlib.file_digest(stored, "sha256").hexdigest() != digest.hexdigest()
            ):
                raise BusinessError("视频保存不完整，请重试。")
        with transaction.atomic():
            # Serialize quota, duplicate detection and reassignment checks per order.
            Trade.objects.select_for_update().get(pk=trade_id)
            trade = scoped_trade(access, trade_id)
            existing = SupplierVideo.objects.filter(trade=trade, sha256=digest.hexdigest()).first()
            if existing:
                target.unlink()
                return existing
            if trade.supplier_videos.count() >= 10:
                raise BusinessError("每单最多保存 10 个视频，请联系店主。")
            video = SupplierVideo.objects.create(
                trade=trade,
                access=access,
                storage_name=name,
                original_name=Path(upload.name.replace("\\", "/")).name[:200],
                size=size,
                sha256=digest.hexdigest(),
                mime_type=mime,
            )
            record_event(
                access.batch.actor,
                "supplier.video_saved",
                video,
                trade_id=str(trade.pk),
                access_id=str(access.pk),
                sha256=video.sha256,
            )
        return video
    except Exception:
        target.unlink(missing_ok=True)
        raise


def submit_dispatch(access, trade_id, code, waybill):
    choices = {r["code"]: r["name"] for r in carriers()}
    waybill = waybill.strip().upper()
    if code not in choices or not re.fullmatch(r"[A-Za-z0-9-]{6,60}", waybill):
        raise BusinessError("请选择快递公司，并填写 6—60 位字母、数字或横线组成的单号。")
    with transaction.atomic():
        Trade.objects.select_for_update().get(pk=trade_id)
        trade = scoped_trade(access, trade_id)
        previous = SupplierDispatch.objects.select_for_update().filter(trade=trade).first()
        if previous and previous.state in ("READY", "SENDING", "UNKNOWN", "SUCCESS"):
            if previous.waybill != waybill or previous.express_code != code:
                raise BusinessError("本单已提交，不能重复更改单号；需要修改请联系店主。")
            return previous
        if trade.status != "SHIPPING" or not trade.platform_id:
            raise BusinessError("本单当前不允许提交发货，请联系店主核对状态。")
        check_shipping_snapshot(access, trade)
        dispatch, _ = SupplierDispatch.objects.update_or_create(
            trade=trade,
            defaults={
                "access": access,
                "express_code": code,
                "express_name": choices[code],
                "waybill": waybill,
                "state": "READY",
                "message": "已保存，等待提交平台",
                "submitted_at": None,
                "confirmed_at": None,
            },
        )
        record_event(
            access.batch.actor,
            "supplier.shipping_requested",
            dispatch,
            access_id=str(access.pk),
            trade_id=str(trade.pk),
        )
        # Periodic worker also drains READY records if broker delivery is unavailable.
        transaction.on_commit(lambda: enqueue_dispatch(dispatch.pk))
    return dispatch


def enqueue_dispatch(pk):
    from .tasks import dispatch_supplier_order

    try:
        dispatch_supplier_order.delay(str(pk))
    except Exception:
        pass  # Durable READY row remains visible and is picked up by the periodic worker.


def process_dispatch(pk):
    with transaction.atomic():
        dispatch = SupplierDispatch.objects.select_for_update().get(pk=pk)
        if dispatch.state != "READY":
            return
        dispatch.state = "SENDING"
        dispatch.message = "正在核对订单并提交平台"
        dispatch.submitted_at = timezone.now()
        dispatch.save()
    attempted = False
    try:
        access = SupplierAccess.objects.select_related("batch__shop").get(pk=dispatch.access_id)
        trade = scoped_trade(access, dispatch.trade_id)
        refresh_order(trade.platform)
        trade = scoped_trade(access, dispatch.trade_id)
        data = effective_order_data(trade.platform)
        check_shipping_snapshot(access, trade)
        if (
            data.get("order_status") != 12
            or data.get("refund_status") not in (0, 4)
            or trade.status != "SHIPPING"
            or trade.shipped_at
        ):
            raise BusinessError("平台订单已发货、退款中或状态已变化，未提交发货，请联系店主。")
        # SENDING was persisted before any write. Once attempted, uncertain results are never retried automatically.
        attempted = True
        XgjClient().call(
            "ship",
            {
                "order_no": trade.number,
                "waybill_no": dispatch.waybill,
                "express_code": dispatch.express_code,
                "express_name": dispatch.express_name,
            },
            seller=trade.platform.connection.seller_id,
        )
        SupplierDispatch.objects.filter(pk=pk).update(
            state="UNKNOWN", message="平台已受理，正在确认发货结果"
        )
        reconcile_dispatch(pk)
    except (APIError, BusinessError) as exc:
        uncertain = attempted and not isinstance(exc, APIRejected)
        SupplierDispatch.objects.filter(pk=pk).update(
            state="UNKNOWN" if uncertain else "FAILED",
            message=(
                f"提交结果待核对：{exc} 请勿重复发货，联系店主核对平台。"
                if uncertain
                else f"未提交发货：{exc}"
            )[:300],
        )
    except Exception:
        SupplierDispatch.objects.filter(pk=pk).update(
            state="UNKNOWN", message="处理意外中断，正在核对平台结果；不会重复提交。"
        )


def reconcile_dispatch(pk):
    dispatch = SupplierDispatch.objects.select_related("trade__platform__connection").get(pk=pk)
    if dispatch.state not in ("UNKNOWN", "SENDING"):
        return
    # Rotate uncertain requests so more than 20 pending rows cannot starve later ones.
    SupplierDispatch.objects.filter(pk=pk).update(updated_at=timezone.now())
    try:
        row = refresh_order(dispatch.trade.platform)
    except (APIError, BusinessError):
        return
    if row.snapshot.get("order_status") in (21, 22) and row.snapshot.get("consign_time"):
        if (
            row.snapshot.get("waybill_no") == dispatch.waybill
            and row.snapshot.get("express_code") == dispatch.express_code
        ):
            SupplierDispatch.objects.filter(pk=pk).update(
                state="SUCCESS", message="平台发货成功", confirmed_at=timezone.now()
            )
            record_event(
                dispatch.access.batch.actor,
                "supplier.shipping_confirmed",
                dispatch,
                trade_id=str(dispatch.trade_id),
            )
        else:
            SupplierDispatch.objects.filter(pk=pk).update(
                state="UNKNOWN", message="平台已发货，但物流信息不一致，请联系店主核对。"
            )
