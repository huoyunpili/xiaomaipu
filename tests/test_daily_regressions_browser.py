import os
from pathlib import Path

import pytest
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright

from app.accounts.models import User
from app.shops.models import Shop
from app.workbench.models import Trade


@pytest.mark.browser
class TestDailyRegressionsBrowser(StaticLiveServerTestCase):
    def test_inline_recovery_failure_success_reload_and_mobile(self):
        call_command("bootstrap")
        User.objects.create_user("regression-owner", password="isolated-test-only", role="ADMIN")
        trade = Trade.objects.create(
            shop=Shop.objects.get(is_active=True),
            number="90001",
            title="回归测试商品",
            status="REFUNDING",
            refund_type=2,
            paid_at=timezone.now(),
            paid_fen=36775,
            unit_cost_fen=32000,
            supplier="测试供应商",
            note="保留原备注",
            issue="暂停发货和回款参考",
        )
        output = Path("artifacts/daily-regressions")
        output.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel=os.environ.get("PLAYWRIGHT_CHANNEL") or None)
            page = browser.new_page(viewport={"width": 390, "height": 844})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(self.live_server_url)
            page.get_by_label("用户名").fill("regression-owner")
            page.get_by_label("密码").fill("isolated-test-only")
            page.get_by_role("button", name="登录", exact=True).click()
            page.goto(self.live_server_url + reverse("wb-refunds"))
            expect(page.get_by_text("平台未提供", exact=False)).to_have_count(0)
            expect(page.get_by_text("暂停发货和回款参考", exact=False)).to_have_count(0)
            select = page.locator(".wb-recovery-controls select")
            button = page.locator("[data-save-recovery]")
            status = page.locator(".wb-recovery")
            expect(status).to_have_css("color", "rgb(185, 28, 28)")
            expect(button).to_be_disabled()
            select.select_option("yes")
            endpoint = "**" + reverse("wb-recovery", args=[trade.pk])
            page.route(
                endpoint,
                lambda route: route.fulfill(
                    status=503, content_type="application/json", body='{"error":"test failure"}'
                ),
            )
            button.click()
            expect(page.locator("[data-recovery-feedback]")).to_have_text("test failure")
            expect(status).to_contain_text("未追回")
            expect(button).to_be_enabled()
            page.unroute(endpoint)
            button.click()
            expect(page.locator("[data-recovery-feedback]")).to_have_text("已保存")
            expect(status).to_have_css("color", "rgb(21, 128, 61)")
            expect(page.locator("[data-recovery-count]")).to_have_text("0")
            page.reload()
            expect(select).to_have_value("yes")
            for width in (390, 1365):
                page.set_viewport_size({"width": width, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(output / f"recovery-{width}.png"), full_page=True)
            select.select_option("no")
            button.click()
            expect(status).to_have_css("color", "rgb(185, 28, 28)")
            expect(page.locator("[data-recovery-count]")).to_have_text("1")
            assert not errors
            browser.close()
        trade.refresh_from_db()
        assert trade.recovered_at is None
        assert trade.note == "保留原备注" and trade.supplier == "测试供应商"
