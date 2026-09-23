"""Xian Guanjia transport. Never expose signed URLs or response errors."""

import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings


class APIError(Exception):
    def __init__(self, message, *, retryable=False):
        super().__init__(message)
        self.retryable = retryable


class APIRejected(APIError):
    """Platform explicitly rejected the operation with a nonzero business code."""

    def __init__(self, message, *, code=None, operation="", diagnostic=""):
        super().__init__(message)
        self.code = code
        self.operation = operation
        self.diagnostic = diagnostic


def private_diagnostic(message, body, key, secret):
    """Redacted provider text for the local administrator, never the public portal/logs."""
    if not isinstance(message, str):
        return "平台未返回文字说明"
    message = message[:2000]
    for value in (key, secret, *(body or {}).values()):
        if isinstance(value, str) and value:
            message = message.replace(value, "[已隐藏]")
    message = re.sub(r"https?://\S+|[A-Za-z0-9_=-]{24,}|\d{7,}", "[已隐藏]", message)
    message = re.sub(
        r"(?i)(appid|sign|secret|token|password|mobile|phone|密码|姓名|地址)\s*[:=：]\s*[^\s,，;；]+",
        r"\1=[已隐藏]",
        message,
    )
    return " ".join(message.split())[:500]


def rejection_hint(code, operation, message):
    message = message if isinstance(message, str) else ""
    if operation == "ship":
        if any(
            word in message
            for word in (
                "寄件",
                "发货地址",
                "ship_name",
                "ship_mobile",
                "ship_address",
                "ship_district",
            )
        ):
            return f"闲管家拒绝发货（错误 {code}）：请店主检查闲管家的默认寄件人、电话和发货地址。"
        if any(
            word in message for word in ("快递公司", "物流公司", "express_code", "express_name")
        ):
            return f"闲管家拒绝发货（错误 {code}）：请核对快递公司。"
        if any(word in message for word in ("快递单号", "物流单号", "waybill_no")):
            return f"闲管家拒绝发货（错误 {code}）：请核对快递单号。"
    return BUSINESS_ERROR_MESSAGES.get(
        code, f"闲管家拒绝了请求（错误 {code}），原因尚未确认，请店主核对平台配置和请求参数。"
    )


BUSINESS_ERROR_MESSAGES = {
    100008: (
        "闲管家拒绝了订单接口（错误 100008）。系统已停止自动重试，避免反复报错；"
        "请在闲管家确认当前店铺已授权且套餐包含订单 API，然后重新验证授权并开启同步。"
        "现有本地数据不会丢失。"
    ),
}


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
        "ship": "/api/open/order/ship",
        "express": "/api/open/express/companies",
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
            raise APIError("平台网络暂不可用，请稍后重试闲管家同步。", retryable=True) from None
        if len(content) > 4 * 1024 * 1024:
            raise APIError("平台响应过大，已停止处理。")
        try:
            payload = json.loads(content)
        except (ValueError, UnicodeError):
            raise APIError("平台响应格式异常。") from None
        if not isinstance(payload, dict) or type(payload.get("code")) is not int:
            raise APIError("平台响应缺少结果代码。")
        if payload["code"] != 0:
            code = payload["code"]
            raise APIRejected(
                rejection_hint(code, operation, payload.get("msg")),
                code=code,
                operation=operation,
                diagnostic=private_diagnostic(payload.get("msg"), body, key, secret),
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise APIError("平台返回数据格式异常。")
        return data


def rows(data):
    result = data.get("list")
    if not isinstance(result, list) or any(not isinstance(row, dict) for row in result):
        raise APIError("平台列表格式异常，未推进同步进度。")
    return result
