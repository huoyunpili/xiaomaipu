import ipaddress
import socket
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
from xml.etree import ElementTree

from celery import shared_task
from django.utils import timezone

from app.catalog.models import SKU

from .models import IntelArticle, IntelSource


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_feed(url):
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise ValueError("仅支持标准 HTTPS 公开信息源。")
    addresses = socket.getaddrinfo(parsed.hostname, 443)
    if not addresses or any(
        not ipaddress.ip_address(result[4][0]).is_global for result in addresses
    ):
        raise ValueError("信息源必须是公开网络地址。")
    request = Request(
        url,
        headers={
            "User-Agent": "SellerBackoffice/0.3 feed-reader",
            "Accept": "application/rss+xml,application/atom+xml",
        },
    )
    with build_opener(NoRedirect()).open(request, timeout=15) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("信息源内容超过大小限制。")
    return raw


def ingest_feed(source, raw):
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("不支持包含实体声明的信息源。")
    root = ElementTree.fromstring(raw)
    products = list(
        SKU.objects.filter(is_active=True, balance__on_hand_qty__gt=0).select_related("product")
    )
    entries = root.findall(".//item") or root.findall("{http://www.w3.org/2005/Atom}entry")
    for entry in entries[:100]:

        def value(tag, entry=entry):
            element = entry.find(tag)
            if element is None:
                element = entry.find("{http://www.w3.org/2005/Atom}" + tag)
            return (
                "" if element is None else (element.text or element.attrib.get("href", "")).strip()
            )

        title, url = value("title")[:300], value("link")[:1000]
        if not title or urlparse(url).scheme not in ("http", "https"):
            continue
        matches = [
            sku
            for sku in products
            if len(sku.product.name) >= 3 and sku.product.name.casefold() in title.casefold()
        ]
        IntelArticle.objects.get_or_create(
            url=url,
            defaults={
                "source": source,
                "title": title,
                "published": (value("pubDate") or value("published") or value("updated"))[:100],
                "matched_sku": matches[0] if len(matches) == 1 else None,
            },
        )


@shared_task
def collect_market_intel():
    for source in IntelSource.objects.filter(enabled=True):
        try:
            ingest_feed(source, read_feed(source.url))
        except Exception:
            source.error = "读取失败，请核对公开 RSS / Atom 地址或稍后重试。"
        else:
            source.last_success = timezone.now()
            source.error = ""
        source.save(update_fields=["last_success", "error", "updated_at"])
