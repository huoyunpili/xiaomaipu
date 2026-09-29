"""Local API credentials shared by the web process and background workers."""

import json
import os
import tempfile
import threading
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.views.decorators.debug import sensitive_variables

from app.accounts.policies import require_admin
from app.common.business import BusinessError, record_event

_write_lock = threading.Lock()


@sensitive_variables()
def read_credentials():
    location = settings.XGJ_CREDENTIALS_FILE
    if location:
        try:
            payload = json.loads(Path(location).read_text(encoding="utf-8"))
            key, secret = payload["app_key"], payload["app_secret"]
            if not isinstance(key, str) or not isinstance(secret, str) or not key or not secret:
                raise ValueError
            return key, secret
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError):
            raise BusinessError(
                "本机 API 配置无法读取，请重新填写 AppKey 和 AppSecret 后保存。"
            ) from None
    return settings.XGJ_APP_KEY, settings.XGJ_APP_SECRET


@sensitive_variables()
def save_credentials(*, actor, key, secret):
    """Save an entire credential pair atomically; never audit credential values."""
    from app.shops.models import Shop

    from .models import Connection

    require_admin(actor)
    if not settings.XGJ_CREDENTIALS_FILE:
        raise BusinessError("当前运行环境未启用本机 API 配置保存。")
    with _write_lock:
        try:
            previous = read_credentials()
        except BusinessError:
            previous = ("", "")
        if not key:
            key = previous[0]
        if not secret:
            if key != previous[0]:
                raise BusinessError("更换 AppKey 时，请同时填写对应的 AppSecret。")
            secret = previous[1]
        if not key or not secret:
            raise BusinessError("首次接入请同时填写 AppKey 和 AppSecret。")
        if (key, secret) == previous:
            return False
        path = Path(settings.XGJ_CREDENTIALS_FILE)
        temporary = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, prefix=".xgj-", delete=False
            ) as stream:
                temporary = Path(stream.name)
                json.dump({"app_key": key, "app_secret": secret}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            with transaction.atomic():
                shop = Shop.objects.get(is_active=True)
                Connection.objects.filter(shop=shop).update(
                    enabled=False, error="API 配置已更新，请验证连接，成功后自动恢复同步。"
                )
                record_event(actor, "xgj.api_configured", shop)
                os.replace(temporary, path)
        except OSError:
            raise BusinessError("API 配置未保存成功，请检查本机数据目录是否可写后重试。") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return True
