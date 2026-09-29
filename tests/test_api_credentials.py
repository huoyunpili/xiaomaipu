import json
import time
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from django.test import Client
from django.urls import reverse

from app.audit.models import AuditEvent
from app.common.business import BusinessError
from app.integrations.client import XgjClient, signature
from app.integrations.credentials import read_credentials, save_credentials
from tests.test_integrations import connection  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def credential_file(settings, tmp_path):
    settings.XGJ_CREDENTIALS_FILE = tmp_path / "xgj-api.json"
    settings.XGJ_APP_KEY = ""
    settings.XGJ_APP_SECRET = ""
    return settings.XGJ_CREDENTIALS_FILE


def test_save_credentials_is_local_admin_only_and_never_echoes_secrets(
    credential_file,
    client,
    connection,  # noqa: F811
    admin_user,
    operator,
):
    connection.enabled = True
    connection.save()
    url = reverse("xgj-operate", args=["save-api"])
    payload = {"app_key": "synthetic-app-key", "app_secret": "synthetic-app-secret"}
    assert client.post(url, payload).status_code == 302
    client.force_login(operator)
    assert client.post(url, payload).status_code == 403
    assert not credential_file.exists()
    client.force_login(admin_user)
    with patch("app.integrations.client.XgjClient.call") as api:
        response = client.post(url, payload, follow=True)
    assert response.status_code == 200 and not api.called
    assert read_credentials() == tuple(payload.values())
    html = response.content.decode()
    assert "手动填写 API 信息" in html and "API 信息已配置" in html
    assert 'type="password"' in html and "验证 API 连接" in html
    for value in payload.values():
        assert value not in html
        assert value not in json.dumps(list(AuditEvent.objects.values("action", "details")))
    connection.refresh_from_db()
    assert not connection.enabled and connection.seller_id == "1234"
    assert AuditEvent.objects.filter(action="xgj.api_configured").get().details == {}


def test_save_requires_csrf(credential_file, admin_user):
    client = Client(enforce_csrf_checks=True)
    client.force_login(admin_user)
    assert client.post(reverse("xgj-operate", args=["save-api"]), {}).status_code == 403
    assert not credential_file.exists()


def test_existing_environment_credentials_are_preserved_and_hidden(
    credential_file, settings, client, admin_user, shop
):
    settings.XGJ_APP_KEY = "existing-synthetic-key"
    settings.XGJ_APP_SECRET = "existing-synthetic-secret"
    client.force_login(admin_user)
    response = client.post(reverse("xgj-operate", args=["save-api"]), {}, follow=True)
    assert "已保留现有 API 配置" in response.content.decode()
    assert not credential_file.exists()
    assert read_credentials() == (settings.XGJ_APP_KEY, settings.XGJ_APP_SECRET)
    assert settings.XGJ_APP_SECRET.encode() not in response.content
    with pytest.raises(BusinessError, match="同时填写"):
        save_credentials(actor=admin_user, key="new-key", secret="")
    assert not credential_file.exists()


def test_atomic_failure_preserves_previous_pair_and_sync_state(
    credential_file,
    connection,  # noqa: F811
    admin_user,
):
    credential_file.write_text(json.dumps({"app_key": "old-key", "app_secret": "old-secret"}))
    connection.enabled = True
    connection.save()
    with patch("app.integrations.credentials.os.replace", side_effect=PermissionError):
        with pytest.raises(BusinessError, match="未保存成功"):
            save_credentials(actor=admin_user, key="new-key", secret="new-secret")
    assert read_credentials() == ("old-key", "old-secret")
    connection.refresh_from_db()
    assert connection.enabled
    assert not AuditEvent.objects.filter(action="xgj.api_configured").exists()
    assert not list(credential_file.parent.glob(".xgj-*"))


def test_saved_pair_is_used_by_client_and_webhook_without_restart(
    credential_file,
    connection,  # noqa: F811
    admin_user,
    client,
):
    transport = MagicMock()
    transport.open.return_value.__enter__.return_value.read.return_value = b'{"code":0,"data":{}}'
    api = XgjClient()
    for key, secret in [("first-key", "first-secret"), ("second-key", "second-secret")]:
        save_credentials(actor=admin_user, key=key, secret=secret)
        with patch("urllib.request.build_opener", return_value=transport):
            api.call("orders", {"page_no": 1}, seller=connection.seller_id)
        request = transport.open.call_args.args[0]
        query = parse_qs(urlsplit(request.full_url).query)
        assert query["appid"] == [key]
        assert query["sign"] == [
            signature(key, secret, int(query["timestamp"][0]), request.data, connection.seller_id)
        ]
    body = json.dumps({"order_no": "123456789"}).encode()
    stamp = int(time.time())
    for key, secret, expected in [
        ("second-key", "second-secret", 200),
        ("first-key", "first-secret", 403),
    ]:
        signed = signature(key, secret, stamp, body)
        response = client.post(
            f"/integrations/xgj/push/?appid={key}&timestamp={stamp}&sign={signed}",
            data=body,
            content_type="application/json",
        )
        assert response.status_code == expected


def test_corrupt_file_requires_repair_instead_of_silent_fallback(
    credential_file, settings, admin_user, shop
):
    settings.XGJ_APP_KEY, settings.XGJ_APP_SECRET = "old-key", "old-secret"
    credential_file.write_text("invalid-json")
    with pytest.raises(BusinessError, match="无法读取"):
        read_credentials()
    save_credentials(actor=admin_user, key="repaired-key", secret="repaired-secret")
    assert read_credentials() == ("repaired-key", "repaired-secret")


@pytest.mark.parametrize(
    "payload",
    [
        {"app_key": "new-key"},
        {"app_secret": "new-secret"},
        {"app_key": "bad\nkey", "app_secret": "test-secret"},
    ],
)
def test_incomplete_or_invalid_input_does_not_write(
    credential_file, admin_user, shop, client, payload
):
    client.force_login(admin_user)
    client.post(reverse("xgj-operate", args=["save-api"]), payload)
    assert not credential_file.exists()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_browser_manual_api_setup(credential_file, shop, admin_user, client, live_server):
    from pathlib import Path

    from playwright.sync_api import expect, sync_playwright

    client.force_login(admin_user)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        context.add_cookies(
            [
                {
                    "name": "sessionid",
                    "value": client.cookies["sessionid"].value,
                    "url": live_server.url,
                }
            ]
        )
        page = context.new_page()
        page.goto(live_server.url + reverse("xgj-home"))
        expect(page.get_by_role("button", name="验证 API 连接")).to_be_disabled()
        page.locator("#id_app_key").fill("browser-key")
        page.locator("#id_app_secret").fill("browser-secret")
        page.get_by_role("button", name="保存 API 配置").click()
        expect(page.get_by_role("button", name="验证 API 连接")).to_be_enabled()
        expect(page.locator("#id_app_secret")).to_have_value("")
        with (
            patch(
                "app.integrations.services.XgjClient.call",
                return_value={"list": [{"is_valid": True, "authorize_id": 1234}]},
            ),
            patch("app.integrations.tasks.sync_orders.delay") as schedule,
        ):
            page.get_by_role("button", name="验证 API 连接").click()
            expect(
                page.get_by_text(
                    "API 连接验证成功，已自动启动订单同步，之后每 5 分钟更新。", exact=True
                )
            ).to_be_visible()
            assert schedule.called
            expect(page.get_by_role("button", name="开启每 5 分钟同步")).to_have_count(0)
        output = Path("artifacts/browser")
        output.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(output / "manual-api-desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(output / "manual-api-mobile.png"), full_page=True)
        browser.close()
