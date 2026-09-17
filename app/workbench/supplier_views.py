from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.staticfiles import finders
from django.core.cache import cache
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from app.accounts.policies import require_admin
from app.common.business import BusinessError, record_event
from app.integrations.client import APIError

from .courier_rules import RULES
from .models import ExportBatch, SupplierAccess, SupplierDispatch, SupplierVideo
from .product_images import trade_image
from .supplier_service import (
    carriers,
    ensure_access,
    save_video,
    scoped_trade,
    submit_dispatch,
    valid_access,
    video_path,
)


def access_for(token):
    access = get_object_or_404(
        SupplierAccess.objects.select_related("batch__shop", "batch__actor"), token=token
    )
    try:
        valid_access(access)
    except BusinessError as exc:
        raise Http404 from exc
    return access


@require_GET
def gateway_health(request):
    if settings.ROOT_URLCONF != "app.config.supplier_urls":
        raise Http404
    return JsonResponse({"service": "supplier-upload-gateway"})


def dispatch_info(trade):
    dispatch = SupplierDispatch.objects.filter(trade=trade).first()
    return {
        "state": dispatch.state if dispatch else "",
        "message": dispatch.message if dispatch else "未提交单号",
        "waybill": dispatch.waybill if dispatch else "",
        "express_code": dispatch.express_code if dispatch else "",
    }


def limited(access):
    key = f"supplier-write:{access.pk}:{timezone.now().strftime('%Y%m%d%H%M')}"
    if cache.add(key, 1, 120):
        return False
    return cache.incr(key) > 40


@require_GET
def portal(request, token):
    access = access_for(token)
    items = []
    for row in access.batch.snapshot:
        try:
            trade = scoped_trade(access, row["id"])
        except BusinessError:
            continue
        items.append(
            {
                "trade": trade,
                "dispatch": dispatch_info(trade),
                "videos": trade.supplier_videos.order_by("created_at"),
            }
        )
    try:
        choices, error = carriers(), ""
    except APIError:
        choices, error = [], "快递公司列表暂时不可用，可先保存视频，稍后刷新提交单号。"
    response = render(
        request,
        "supplier/portal.html",
        {
            "access": access,
            "items": items,
            "carriers": choices,
            "error": error,
            "courier_rules": RULES,
        },
    )
    response["Referrer-Policy"] = "no-referrer"
    response["Cache-Control"] = "no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


@require_POST
def upload(request, token, pk):
    access = access_for(token)
    if limited(access):
        return JsonResponse({"error": "操作较频繁，请稍后重试。"}, status=429)
    try:
        scoped_trade(access, pk)
        video = save_video(access, pk, request.FILES.get("video"))
        return JsonResponse(
            {"name": video.original_name, "message": "视频已保存到店主电脑", "sha256": video.sha256}
        )
    except BusinessError as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_POST
def shipping(request, token, pk):
    access = access_for(token)
    if limited(access):
        return JsonResponse({"error": "操作较频繁，请稍后重试。"}, status=429)
    try:
        trade = scoped_trade(access, pk)
        submit_dispatch(
            access, pk, request.POST.get("express_code", ""), request.POST.get("waybill", "")
        )
        return JsonResponse(dispatch_info(trade))
    except (BusinessError, APIError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_GET
def status(request, token, pk):
    try:
        trade = scoped_trade(access_for(token), pk)
    except BusinessError as exc:
        raise Http404 from exc
    return JsonResponse(dispatch_info(trade))


@require_GET
def product_image(request, token, pk):
    try:
        trade = scoped_trade(access_for(token), pk)
        path, mime = trade_image(trade)
    except (BusinessError, APIError, ValueError, OSError) as exc:
        raise Http404 from exc
    return FileResponse(path.open("rb"), content_type=mime)


@require_GET
def asset(request, name):
    if name not in ("supplier.js", "supplier.css"):
        raise Http404
    path = finders.find(name)
    if not path:
        path = settings.STATIC_ROOT / name
    return FileResponse(
        open(path, "rb"), content_type="text/javascript" if name.endswith(".js") else "text/css"
    )


def batch_text(batch, url=""):
    lines = [f"发货单 | {batch.supplier}", f"共 {len(batch.snapshot)} 单", ""]
    for index, row in enumerate(batch.snapshot, 1):
        lines.extend(
            [
                f"{index}. {row.get('title') or '旧清单缺少商品名称，请重新导出'}",
                f"规格：{row['spec']}" if row["spec"] else "",
                f"数量：{row['quantity']} 件",
                f"订单号：{row['number']}",
                f"收件人：{row['receiver']}  电话：{row['phone']}",
                f"地址：{row['address']}",
                f"备注：{row['note']}" if row.get("note") else "",
                "",
            ]
        )
    if url:
        lines.extend(
            [
                "填写单号和上传发货视频：",
                url,
                "可分单保存、之后继续补传。保存单号会自动向闲鱼提交发货，请核对后操作。",
            ]
        )
    return "\n".join(lines)


def batch_context(batch):
    config = getattr(batch.shop, "workspacesettings", None)
    base = config.supplier_base_url.rstrip("/") if config else ""
    access = SupplierAccess.objects.filter(batch=batch).first()
    url = ""
    if access and base and not access.revoked_at and access.expires_at > timezone.now():
        url = base + reverse("supplier-portal", args=[access.token])
    return {
        "shipping_text": batch_text(batch, url),
        "supplier_url": url,
        "supplier_access": access,
        "supplier_base": base,
    }


@login_required
@require_POST
def create_link(request, pk):
    from .views import active_shop

    require_admin(request.user)
    batch = get_object_or_404(ExportBatch, pk=pk, shop=active_shop(), kind="shipping")
    ensure_access(batch)
    return redirect("wb-batch", pk=pk)


@login_required
@require_POST
def revoke(request, pk):
    from .views import active_shop

    require_admin(request.user)
    access = get_object_or_404(SupplierAccess, batch_id=pk, batch__shop=active_shop())
    access.revoked_at = timezone.now()
    access.save(update_fields=["revoked_at"])
    record_event(request.user, "supplier.link_revoked", access)
    return redirect("wb-batch", pk=pk)


@login_required
@require_GET
def download_text(request, pk):
    from .views import active_shop

    require_admin(request.user)
    batch = get_object_or_404(ExportBatch, pk=pk, shop=active_shop(), kind="shipping")
    response = HttpResponse(
        batch_context(batch)["shipping_text"], content_type="text/plain; charset=utf-8"
    )
    response["Content-Disposition"] = 'attachment; filename="shipping.txt"'
    return response


@login_required
@require_GET
def video_download(request, pk):
    from .views import active_shop

    require_admin(request.user)
    video = get_object_or_404(SupplierVideo, pk=pk, trade__shop=active_shop())
    path = video_path(video.storage_name)
    if not path.is_file():
        raise Http404
    return FileResponse(
        path.open("rb"),
        as_attachment=True,
        filename=video.original_name,
        content_type=video.mime_type,
    )
