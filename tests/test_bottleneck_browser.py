import os
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.management import call_command
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright

from app.accounts.models import User
from app.finance.services import record_customer_payment
from tests.test_bottlenecks import platform_order


@pytest.mark.browser
class TestBottleneckBrowser(StaticLiveServerTestCase):
    def test_mobile_card_action_mapping_and_snooze(self):
        call_command("bootstrap")
        actor = User.objects.create_user(
            "bottleneck-owner", password="bottleneck-browser-only", role=User.Role.ADMIN
        )
        order = platform_order(actor)
        record_customer_payment(
            actor=actor,
            submission_key=uuid.uuid4(),
            order_id=order.pk,
            version=order.version,
            amount_fen=order.amount_fen,
            source_ref="BROWSER-K4",
            evidence="页面核对平台付款",
            occurred_at=timezone.now() - timedelta(hours=1),
        )
        screenshots = Path("artifacts/browser")
        screenshots.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                channel=os.environ.get("PLAYWRIGHT_CHANNEL") or None
            )
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.goto(self.live_server_url)
            page.get_by_label("用户名").fill("bottleneck-owner")
            page.get_by_label("密码").fill("bottleneck-browser-only")
            page.get_by_role("button", name="登录", exact=True).click()
            # Legacy history remains reachable; it is no longer in the primary navigation.
            page.goto(self.live_server_url + "/operations/")
            expect(page.get_by_role("heading", name="钱和货卡在哪里")).to_be_visible()
            expect(page.get_by_text("K6 售后钱货不同步：暂不可计算", exact=False)).to_be_visible()
            expect(page.get_by_text("K4 · 客户已付款，仍待发", exact=True)).to_be_visible()
            page.get_by_role("link", name="备注 / 稍后提醒").click()
            page.get_by_label("稍后提醒时间（选填）").fill(
                (timezone.localtime() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
            )
            page.get_by_label("跟进备注（选填）").fill("明天核对仓库后发货")
            page.get_by_role("button", name="保存", exact=True).click()
            expect(page.get_by_text("卡点仍未解决", exact=False)).to_be_visible()
            expect(page.get_by_text("明天核对仓库后发货", exact=False)).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.screenshot(path=str(screenshots / "bottlenecks-mobile.png"), full_page=True)
            browser.close()
