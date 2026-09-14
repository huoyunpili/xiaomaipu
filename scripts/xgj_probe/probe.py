"""Read-only probe for the Xian Guanjia Open API.

Secrets are collected with getpass and are never printed or persisted.
Only a deliberately small allow-list of read endpoints is implemented here.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

DEFAULT_BASE_URL = "https://open.goofish.pro"


@dataclass(frozen=True)
class Credentials:
    app_key: str
    app_secret: str


def compact_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def build_signature(
    credentials: Credentials,
    timestamp: int,
    body_text: str,
    seller_id: str | None = None,
) -> str:
    body_md5 = hashlib.md5(body_text.encode("utf-8")).hexdigest()  # noqa: S324
    parts = [credentials.app_key, body_md5, str(timestamp)]
    if seller_id:
        parts.append(seller_id)
    parts.append(credentials.app_secret)
    source = ",".join(parts)
    return hashlib.md5(source.encode("utf-8")).hexdigest()  # noqa: S324


def redact_id(value: Any) -> str:
    text = str(value or "")
    if len(text) <= 6:
        return "***" if text else ""
    return f"{text[:3]}...{text[-3:]}"


def post(
    base_url: str,
    credentials: Credentials,
    path: str,
    body: dict[str, Any] | None,
    *,
    seller_id: str | None = None,
) -> dict[str, Any]:
    body_text = compact_json(body or {})
    timestamp = int(time.time())
    query = {
        "appid": credentials.app_key,
        "timestamp": str(timestamp),
        "sign": build_signature(credentials, timestamp, body_text, seller_id),
    }
    if seller_id:
        query["seller_id"] = seller_id

    url = f"{base_url.rstrip('/')}{path}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(
        url,
        data=body_text.encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "xgj-readonly-probe/0.1",
            "sdk_version": "xgj-sdk-python-v1.0",
        },
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        status = exc.code
    elapsed_ms = round((time.monotonic() - started) * 1000)

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"HTTP {status}; response was not JSON; elapsed={elapsed_ms}ms") from exc

    if not isinstance(payload, dict):
        raise RuntimeError(f"HTTP {status}; unexpected response type; elapsed={elapsed_ms}ms")
    payload["_probe_http_status"] = status
    payload["_probe_elapsed_ms"] = elapsed_ms
    return payload


def get_credentials() -> Credentials:
    app_key = getpass.getpass("AppKey（隐藏输入）: ").strip()
    app_secret = getpass.getpass("AppSecret（隐藏输入）: ").strip()
    if not app_key or not app_secret:
        raise SystemExit("AppKey 和 AppSecret 都不能为空。")
    return Credentials(app_key=app_key, app_secret=app_secret)


def fetch_stores(
    base_url: str, credentials: Credentials
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    # The official SDK serializes an empty request body as the literal `{}`.
    payload = post(base_url, credentials, "/api/open/user/authorize/list", {})
    stores = (
        payload.get("data", {}).get("list", []) if isinstance(payload.get("data"), dict) else []
    )
    if not isinstance(stores, list):
        stores = []
    return payload, [item for item in stores if isinstance(item, dict)]


def print_common(payload: dict[str, Any]) -> None:
    print(
        compact_json(
            {
                "http_status": payload.get("_probe_http_status"),
                "elapsed_ms": payload.get("_probe_elapsed_ms"),
                "code": payload.get("code"),
                "msg": payload.get("msg"),
            }
        )
    )


def command_stores(base_url: str, credentials: Credentials) -> int:
    payload, stores = fetch_stores(base_url, credentials)
    print_common(payload)
    summaries = [
        {
            "authorize_id": redact_id(store.get("authorize_id")),
            "shop_name": store.get("shop_name"),
            "is_valid": store.get("is_valid"),
            "is_trial": store.get("is_trial"),
            "is_pro": store.get("is_pro"),
            "is_deposit_enough": store.get("is_deposit_enough"),
            "valid_end_time": store.get("valid_end_time"),
            "service_support": store.get("service_support"),
        }
        for store in stores
    ]
    print(compact_json({"store_count": len(summaries), "stores": summaries}))
    return 0 if payload.get("code") == 0 else 1


def command_orders(base_url: str, credentials: Credentials) -> int:
    store_payload, stores = fetch_stores(base_url, credentials)
    print_common(store_payload)
    valid_stores = [store for store in stores if store.get("is_valid")]
    if not valid_stores:
        print(compact_json({"error": "no_valid_authorized_store"}))
        return 2

    seller_id = str(valid_stores[0].get("authorize_id") or "")
    payload = post(
        base_url,
        credentials,
        "/api/open/order/list",
        {"page_size": 10, "page_no": 1},
        seller_id=seller_id,
    )
    print_common(payload)
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    orders = data.get("list", []) if isinstance(data, dict) else []
    if not isinstance(orders, list):
        orders = []
    summaries = []
    for order in orders:
        if not isinstance(order, dict):
            continue
        summaries.append(
            {
                "order_no": redact_id(order.get("order_no")),
                "order_status": order.get("order_status"),
                "refund_status": order.get("refund_status"),
                "order_time": order.get("order_time"),
                "update_time": order.get("update_time"),
                "product_id": redact_id(order.get("product_id")),
            }
        )
    print(
        compact_json(
            {
                "seller_id": redact_id(seller_id),
                "returned_order_count": len(summaries),
                "orders": summaries,
            }
        )
    )
    return 0 if payload.get("code") == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="闲管家开放 API 只读探针")
    parser.add_argument("command", choices=("stores", "orders"))
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    args = parser.parse_args()
    credentials = get_credentials()
    if args.command == "stores":
        return command_stores(args.base_url, credentials)
    return command_orders(args.base_url, credentials)


if __name__ == "__main__":
    raise SystemExit(main())
