"""Authenticated local cache for the platform's product CDN images."""

import hashlib
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

from django.conf import settings
from django.db import transaction

from app.integrations.client import APIError, NoRedirect, XgjClient

MAX_BYTES = 5 * 1024 * 1024


def restore_image(trade, client=None, *, refresh=False):
    """Fill only image metadata; viewing a thumbnail must not change order facts."""
    if (trade.image and not refresh) or not trade.platform_id:
        return trade.image
    row = trade.platform
    images = row.snapshot.get("goods", {}).get("images", [])
    if refresh or not images:
        data = (client or XgjClient()).call(
            "detail", {"order_no": row.external_order_no}, seller=row.connection.seller_id
        )
        if data.get("order_no") != row.external_order_no:
            raise APIError("平台返回订单与查询订单不一致。")
        from app.integrations.services import normalize

        images = normalize(data).get("goods", {}).get("images", [])
    if not images:
        return ""
    from app.integrations.models import PlatformOrder

    from .models import Trade

    with transaction.atomic():
        current = PlatformOrder.objects.select_for_update().get(pk=row.pk)
        current.snapshot = {
            **current.snapshot,
            "goods": {**current.snapshot.get("goods", {}), "images": images},
        }
        current.save(update_fields=["snapshot"])
        target = Trade.objects.filter(pk=trade.pk)
        if not refresh:
            target = target.filter(image="")
        target.update(image=images[0])
    trade.refresh_from_db(fields=["image"])
    trade.platform = current
    return trade.image


def trade_image(trade, *, refresh_attempted=False):
    restore_image(trade)
    candidates = [trade.image]
    if trade.platform_id:
        candidates.extend(trade.platform.snapshot.get("goods", {}).get("images", []))
    for url in list(dict.fromkeys(candidates))[:3]:
        if not url:
            continue
        try:
            result = cached_image(url)
        except (OSError, ValueError):
            continue
        if url != trade.image:
            from .models import Trade

            Trade.objects.filter(pk=trade.pk).update(image=url)
        return result
    if trade.platform_id and not refresh_attempted:
        restore_image(trade, refresh=True)
        return trade_image(trade, refresh_attempted=True)
    raise ValueError("No available product image")


def image_type(data):
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp" and data[8:12] in (b"avif", b"avis"):
        return "image/avif"
    raise ValueError("Unsupported product image")


def cached_image(url):
    parsed = urlsplit(url)
    if (
        parsed.scheme not in ("http", "https")
        or parsed.hostname != "img.alicdn.com"
        or parsed.port not in (None, 443 if parsed.scheme == "https" else 80)
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Untrusted product image origin")
    # Older platform snapshots use http links; fetch this fixed CDN over HTTPS.
    url = parsed._replace(scheme="https", netloc="img.alicdn.com", fragment="").geturl()
    root = Path(settings.PRIVATE_MEDIA_ROOT).resolve()
    folder = root / "product-images"
    path = folder / hashlib.sha256(url.encode()).hexdigest()
    if path.resolve() != path or folder.is_symlink():
        raise ValueError("Invalid product image path")
    folder.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        try:
            with path.open("rb") as handle:
                return path, image_type(handle.read(32))
        except ValueError:
            path.unlink(missing_ok=True)
    request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "image/*"})
    with build_opener(NoRedirect).open(request, timeout=8) as response:
        data = response.read(MAX_BYTES + 1)
    if not data or len(data) > MAX_BYTES:
        raise ValueError("Product image exceeds size limit")
    mime = image_type(data)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=folder, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    return path, mime
