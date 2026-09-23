from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from app.accounts.policies import require_admin
from app.common.business import BusinessError

from .models import ExportBatch, SupplierVideo


def batch_text(batch):
    """Render the only supplier hand-off supported by the public edition."""
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
    return "\n".join(lines)


@login_required
@require_GET
def download_text(request, pk):
    from .views import active_shop

    require_admin(request.user)
    batch = get_object_or_404(ExportBatch, pk=pk, shop=active_shop(), kind="shipping")
    response = HttpResponse(batch_text(batch), content_type="text/plain; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="shipping.txt"'
    response["Cache-Control"] = "no-store"
    return response


def legacy_video_path(storage_name):
    """Resolve evidence saved by pre-0.6 supplier links without enabling new uploads."""
    root = (Path(settings.PRIVATE_MEDIA_ROOT) / "supplier-evidence").resolve()
    path = (Path(settings.PRIVATE_MEDIA_ROOT) / storage_name).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise BusinessError("历史视频路径无效。") from exc
    return path


@login_required
@require_GET
def video_download(request, pk):
    """Keep authenticated read access to videos that existing RC users already own."""
    from .views import active_shop

    require_admin(request.user)
    video = get_object_or_404(SupplierVideo, pk=pk, trade__shop=active_shop())
    try:
        path = legacy_video_path(video.storage_name)
    except BusinessError as exc:
        raise Http404 from exc
    if not path.is_file():
        raise Http404
    response = FileResponse(
        path.open("rb"),
        as_attachment=True,
        filename=video.original_name,
        content_type=video.mime_type,
    )
    response["Cache-Control"] = "no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
