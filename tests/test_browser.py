import os
from pathlib import Path

import pytest
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.management import call_command
from playwright.sync_api import expect, sync_playwright

from app.accounts.models import User
from app.audit.models import AuditEvent
from app.shops.models import Shop


@pytest.mark.browser
class TestBrowser(StaticLiveServerTestCase):
    def test_login_edit_audit_and_mobile(self):
        call_command("bootstrap")
        User.objects.create_user(
            "browser-owner", password="browser-test-only", role=User.Role.ADMIN
        )
        screenshots = Path("artifacts/browser")
        screenshots.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                channel=os.environ.get("PLAYWRIGHT_CHANNEL") or None
            )
            page = browser.new_page(viewport={"width": 1365, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(self.live_server_url)
            page.get_by_label("用户名").fill("browser-owner")
            page.get_by_label("密码").fill("browser-test-only")
            page.get_by_role("button", name="登录", exact=True).click()
            expect(page.get_by_role("heading", name="我的小卖铺")).to_be_visible()
            expect(page.get_by_role("heading", name="钱款总览")).to_be_visible()
            expect(page.get_by_role("heading", name="货物总览")).to_be_visible()
            expect(page.get_by_role("heading", name="钱货卡点", exact=True)).to_be_visible()
            for label in ("销售", "采购", "库存", "钱货卡点", "经营分析", "数据接入", "设置"):
                expect(page.get_by_text(label, exact=True).first).to_be_visible()
            page.screenshot(path=str(screenshots / "dashboard-desktop.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.screenshot(path=str(screenshots / "dashboard-mobile.png"), full_page=True)
            page.set_viewport_size({"width": 1365, "height": 900})
            page.get_by_text("设置", exact=True).click()
            page.get_by_role("link", name="店铺设置", exact=True).click()
            page.get_by_label("店铺名称").fill("二手数码小铺")
            page.get_by_role("button", name="保存资料").click()
            expect(page.get_by_role("status")).to_have_text("店铺资料已保存。")
            page.screenshot(path=str(screenshots / "settings-desktop.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            expect(page.get_by_role("button", name="保存资料")).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.screenshot(path=str(screenshots / "settings-mobile.png"), full_page=True)
            page.get_by_text("设置", exact=True).click()
            page.get_by_role("link", name="操作记录", exact=True).click()
            expect(page.get_by_text("更新店铺资料", exact=True)).to_be_visible()
            page.get_by_text("设置", exact=True).click()
            page.get_by_role("button", name="退出登录").click()
            expect(page.get_by_role("heading", name="登录经营后台")).to_be_visible()
            assert not errors
            browser.close()
        assert Shop.objects.get(is_active=True).name == "二手数码小铺"
        assert AuditEvent.objects.count() == 1
