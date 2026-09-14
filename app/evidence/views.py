import hashlib
import io
import uuid
from pathlib import Path

import qrcode
import qrcode.image.svg
from django import forms
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.files.storage import FileSystemStorage
from django.http import FileResponse, Http404, HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from app.accounts.policies import require_operator
from app.common.business import BusinessError, record_event
from app.common.forms import SubmissionForm
from app.common.services import IdempotencyConflict, execute_once
from app.orders.models import SalesOrder

from .models import EvidenceVideo


def storage():
    return FileSystemStorage(location=settings.PRIVATE_MEDIA_ROOT)


class UploadForm(SubmissionForm):
    video = forms.FileField(
        label="取证视频（MP4 / WebM，最大 250 MB）",
        widget=forms.ClearableFileInput(attrs={"accept": "video/mp4,video/webm"}),
    )


def save_video(*, actor, submission_key, order_id, video):
    require_operator(actor)
    if not 0 < video.size <= 250 * 1024 * 1024:
        raise BusinessError("视频为空或超过 250 MB，请重新选择。")
    signature = video.read(16)
    video.seek(0)
    mime, extension = (
        ("video/mp4", ".mp4")
        if signature[4:8] == b"ftyp"
        else ("video/webm", ".webm")
        if signature[:4] == b"\x1aE\xdf\xa3"
        else ("", "")
    )
    if not mime:
        raise BusinessError("请选择有效的 MP4 或 WebM 视频文件。")
    digest = hashlib.sha256()
    for chunk in video.chunks():
        digest.update(chunk)
    video.seek(0)
    created_files = []

    def action():
        order = SalesOrder.objects.select_for_update().get(pk=order_id)
        filename = storage().save(f"evidence/{uuid.uuid4().hex}{extension}", video)
        created_files.append(filename)
        EvidenceVideo.objects.filter(order=order, active=True).update(active=False)
        record = EvidenceVideo.objects.create(
            order=order,
            storage_name=filename,
            original_name=Path(video.name).name[:200],
            size=video.size,
            sha256=digest.hexdigest(),
            mime_type=mime,
        )
        record_event(actor, "evidence.uploaded", record, order_id=str(order.pk))
        return {"order_id": str(order.pk), "video_id": str(record.pk)}

    try:
        return execute_once(
            f"evidence.upload:{actor.pk}",
            submission_key,
            dict(order_id=str(order_id), sha256=digest.hexdigest(), size=video.size),
            action,
        )
    except Exception:
        for filename in created_files:
            storage().delete(filename)
        raise


@login_required
@require_http_methods(["GET", "POST"])
def upload(request, order_id):
    order = get_object_or_404(SalesOrder, pk=order_id)
    form = UploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            save_video(actor=request.user, order_id=order.pk, **form.cleaned_data)
        except (BusinessError, IdempotencyConflict) as exc:
            form.add_error(None, str(exc))
        else:
            return redirect("order-detail", order_id=order.pk)
    return render(request, "evidence/upload.html", {"form": form, "order": order})


@login_required
@require_GET
def qr(request, order_id):
    get_object_or_404(SalesOrder, pk=order_id)
    path = reverse("evidence-upload", args=[order_id])
    base_url = settings.PUBLIC_BASE_URL.rstrip("/")
    url = base_url + path if base_url else request.build_absolute_uri(path)
    code = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage)
    output = io.BytesIO()
    code.save(output)
    return HttpResponse(output.getvalue(), content_type="image/svg+xml")


@login_required
@require_GET
def video(request, video_id):
    record = get_object_or_404(EvidenceVideo, pk=video_id)
    if not storage().exists(record.storage_name):
        raise Http404("视频文件不存在，请检查备份。")
    file = storage().open(record.storage_name, "rb")
    response: StreamingHttpResponse
    if request.GET.get("download"):
        response = FileResponse(
            file, as_attachment=True, filename=record.original_name, content_type=record.mime_type
        )
    elif request.headers.get("Range"):
        try:
            unit, positions = request.headers["Range"].split("=", 1)
            left, right = positions.split("-", 1)
            if unit != "bytes" or not left and not right:
                raise ValueError
            start = int(left) if left else max(0, record.size - int(right))
            end = min(int(right), record.size - 1) if left and right else record.size - 1
            if not 0 <= start <= end < record.size:
                raise ValueError
        except ValueError:
            file.close()
            return HttpResponse(status=416, headers={"Content-Range": f"bytes */{record.size}"})

        def chunks():
            try:
                file.seek(start)
                remaining = end - start + 1
                while remaining:
                    chunk = file.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk
            finally:
                file.close()

        response = StreamingHttpResponse(chunks(), status=206, content_type=record.mime_type)
        response["Content-Range"] = f"bytes {start}-{end}/{record.size}"
        response["Content-Length"] = end - start + 1
    else:
        response = FileResponse(file, content_type=record.mime_type)
    response["Accept-Ranges"] = "bytes"
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
