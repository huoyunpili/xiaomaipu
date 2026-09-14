"""Read-only Xian Guanjia transport. Never expose signed URLs or response errors."""

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings


class APIError(Exception):
    def __init__(self, message, *, retryable=False):
        super().__init__(message)
        self.retryable = retryable


def signature(key, secret, timestamp, body, seller=""):
    parts = [key, hashlib.md5(body).hexdigest(), str(timestamp)]
    if seller:
        parts.append(seller)
    parts.append(secret)
    return hashlib.md5(",".join(parts).encode()).hexdigest()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class XgjClient:
    paths = {
        "stores": "/api/open/user/authorize/list",
        "orders": "/api/open/order/list",
        "detail": "/api/open/order/detail",
        "refunds": "/api/open/trade/refund/list",
        "refund_detail": "/api/open/trade/refund/detail",
    }

    def call(self, operation, body=None, seller=""):
        key, secret = settings.XGJ_APP_KEY, settings.XGJ_APP_SECRET
        if not key or not secret:
            raise APIError("未配置闲管家密钥，请检查本地或部署环境配置。")
        raw = json.dumps(body or {}, ensure_ascii=False, separators=(",", ":")).encode()
        stamp = int(time.time())
        query = {
            "appid": key,
            "timestamp": str(stamp),
            "sign": signature(key, secret, stamp, raw, seller),
        }
        if seller:
            query["seller_id"] = seller
        url = (
            "https://open.goofish.pro" + self.paths[operation] + "?" + urllib.parse.urlencode(query)
        )
        request = urllib.request.Request(
            url,
            data=raw,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "sdk_version": "xgj-sdk-python-v1.0",
            },
        )
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=20) as response:
                content = response.read(4 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as exc:
            raise APIError(
                f"平台 HTTP {exc.code}，请稍后重试或检查授权。",
                retryable=exc.code == 429 or exc.code >= 500,
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise APIError("平台网络暂不可用，可重试或使用表格导入。", retryable=True) from None
        if len(content) > 4 * 1024 * 1024:
            raise APIError("平台响应过大，已停止处理。")
        try:
            payload = json.loads(content)
        except (ValueError, UnicodeError):
            raise APIError("平台响应格式异常。") from None
        if not isinstance(payload, dict) or type(payload.get("code")) is not int:
            raise APIError("平台响应缺少结果代码。")
        if payload["code"] != 0:
            raise APIError(f"平台业务错误 {payload['code']}，请检查授权、套餐或接口权限。")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise APIError("平台返回数据格式异常。")
        return data


def rows(data):
    result = data.get("list")
    if not isinstance(result, list) or any(not isinstance(row, dict) for row in result):
        raise APIError("平台列表格式异常，未推进同步进度。")
    return result
