import os
import re
from pathlib import Path

import pytest
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.management import call_command
from playwright.sync_api import expect, sync_playwright

from app.accounts.models import User
from app.integrations.models import Connection
from app.integrations.services import store_order
from app.orders.models import SalesOrder
from app.shops.models import Shop
from tests.test_business import goods
from tests.test_integrations import data


@pytest.mark.browser
class TestIntegrationBrowser(StaticLiveServerTestCase):
    def test_mobile_platform_order_to_draft(self):
        call_command("bootstrap")
        actor = User.objects.create_user(
            "api-owner", password="api-test-only", role=User.Role.ADMIN
        )
        sku, lot = goods(actor)
        connection = Connection.objects.create(
            shop=Shop.objects.get(is_active=True), actor=actor, seller_id="1234"
        )
        row = store_order(connection, data())
        screenshots = Path("artifacts/browser")
        screenshots.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                channel=os.environ.get("PLAYWRIGHT_CHANNEL") or None
            )
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.goto(self.live_server_url)
            page.get_by_label("用户名").fill("api-owner")
            page.get_by_label("密码").fill("api-test-only")
            page.get_by_role("button", name="登录", exact=True).click()
            page.get_by_role("link", name="平台同步", exact=True).click()
            expect(page.get_by_role("heading", name="闲管家订单同步")).to_be_visible()
            page.get_by_role("link", name=row.external_order_no, exact=True).click()
            expect(page.get_by_role("heading", name="核对闲鱼订单")).to_be_visible()
            page.get_by_label("本地商品").select_option(str(sku.pk))
            page.get_by_label("核对成交单价（元）").fill("120.00")
            page.get_by_label("已核对商品、货况、数量及成交金额，创建草稿后继续处理").check()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.screenshot(path=str(screenshots / "xgj-detail-mobile.png"), full_page=True)
            page.get_by_role("button", name="创建或关联销售草稿").click()
            expect(page).to_have_url(re.compile(r"/orders/[0-9a-f-]+/$"))
            browser.close()
        assert SalesOrder.objects.get().status == "DRAFT"
