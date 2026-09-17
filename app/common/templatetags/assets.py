"""Change asset URLs with their contents so UI scripts and styles update together."""

import hashlib
from functools import lru_cache
from pathlib import Path

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()


@lru_cache(maxsize=128)
def content_version(filename: str, modified: int, size: int) -> str:
    return hashlib.sha256(Path(filename).read_bytes()).hexdigest()[:12]


@register.simple_tag
def versioned_static(name: str) -> str:
    url = static(name)
    filename = finders.find(name)
    if not isinstance(filename, str):
        return url
    try:
        stat = Path(filename).stat()
        version = content_version(filename, stat.st_mtime_ns, stat.st_size)
    except OSError:
        return url
    return f"{url}{'&' if '?' in url else '?'}v={version}"
