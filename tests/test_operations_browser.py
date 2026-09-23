import base64
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.management import call_command
from django.test import override_settings
from playwright.sync_api import expect, sync_playwright

from app.accounts.models import User
from tests.test_business import draft, goods


@pytest.mark.browser
class TestOperationsBrowser(StaticLiveServerTestCase):
    def test_customer_video_import_reports_mobile(self):
        call_command("bootstrap")
        actor = User.objects.create_user(
            "ops-owner", password="ops-browser-only", role=User.Role.ADMIN
        )
        sku, lot = goods(actor)
        order = draft(actor, sku)
        screenshots = Path("artifacts/browser")
        screenshots.mkdir(parents=True, exist_ok=True)
        with (
            TemporaryDirectory() as media,
            override_settings(PRIVATE_MEDIA_ROOT=media),
            sync_playwright() as playwright,
        ):
            browser = playwright.chromium.launch(
                channel=os.environ.get("PLAYWRIGHT_CHANNEL") or None
            )
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.goto(self.live_server_url)
            page.get_by_label("用户名").fill("ops-owner")
            page.get_by_label("密码").fill("ops-browser-only")
            page.get_by_role("button", name="登录", exact=True).click()
            page.goto(self.live_server_url + "/customers/")
            page.get_by_role("link", name="新增客户", exact=True).click()
            page.get_by_label("客户称呼", exact=True).fill("复购客户")
            page.get_by_label("标签（自己写）").fill("熟客，可微信联系")
            page.get_by_role("button", name="保存", exact=True).click()
            expect(page.get_by_role("heading", name="复购客户")).to_be_visible()
            page.goto(f"{self.live_server_url}/orders/{order.pk}/evidence/")
            encoded = base64.b64encode(Path("tests/fixtures/flower.webm").read_bytes()).decode()
            page.locator('input[type="file"]').set_input_files(
                {
                    "name": "evidence.webm",
                    "mimeType": "video/webm",
                    "buffer": base64.b64decode(encoded),
                }
            )
            page.get_by_role("button", name="上传并保存").click()
            video = page.locator("video")
            expect(video).to_be_visible()
            original_bytes = base64.b64decode(encoded)
            served = page.request.get(
                self.live_server_url + video.get_attribute("src"), headers={"Range": "bytes=0-"}
            )
            assert served.body() == original_bytes, (
                len(served.body()),
                len(original_bytes),
                served.status,
                served.headers,
            )
            (screenshots / "generated-evidence.webm").write_bytes(original_bytes)
            video_result = video.evaluate(
                "async v => {v.muted = true; try {await v.play(); return 'ok'} catch(e) {return {name:e.name, message:e.message, code:v.error?.code, media:v.error?.message}}}"
            )
            assert video_result == "ok", video_result
            page.wait_for_function("document.querySelector('video').readyState >= 2")
            assert video.evaluate("v => v.videoWidth") > 0
            page.screenshot(path=str(screenshots / "evidence-mobile.png"), full_page=True)
            page.goto(self.live_server_url + "/imports/")
            expect(page.get_by_role("heading", name="闲管家订单同步")).to_be_visible()
            expect(page.get_by_text("本地订单文件导入已停用", exact=False)).to_be_visible()
            page.screenshot(path=str(screenshots / "xgj-history-mobile.png"), full_page=True)
            for route, heading, filename in (
                ("/reports/", "经营报表", "reports-mobile.png"),
                ("/risks/", "商品与库存风险", "risks-mobile.png"),
            ):
                page.goto(self.live_server_url + route)
                expect(page.get_by_role("heading", name=heading, exact=True)).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                page.screenshot(path=str(screenshots / filename), full_page=True)
            browser.close()
