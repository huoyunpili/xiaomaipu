import os
from pathlib import Path

import pytest
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.management import call_command
from playwright.sync_api import expect, sync_playwright

from app.accounts.models import User
from app.inventory.models import InventoryBalance
from app.orders.models import SalesOrder
from tests.test_business import action, draft, goods


@pytest.mark.browser
class TestBusinessBrowser(StaticLiveServerTestCase):
    def test_two_deliveries_keep_separate_tracking(self):
        call_command("bootstrap")
        actor = User.objects.create_user(
            "partial-owner", password="partial-browser-only", role=User.Role.ADMIN
        )
        sku, lot = goods(actor, quantity=2)
        order = draft(actor, sku, quantity=2, lot=lot)
        action(actor, order, "confirm")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                channel=os.environ.get("PLAYWRIGHT_CHANNEL") or None
            )
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.goto(self.live_server_url)
            page.get_by_label("用户名").fill("partial-owner")
            page.get_by_label("密码").fill("partial-browser-only")
            page.get_by_role("button", name="登录", exact=True).click()
            page.goto(f"{self.live_server_url}/orders/{order.pk}/")
            for tracking in ("FIRST-PARCEL", "SECOND-PARCEL"):
                page.get_by_role("link", name="发货 / 交付", exact=True).click()
                page.get_by_label("本次发货数量", exact=True).fill("1")
                page.get_by_label("交付方式", exact=True).select_option("EXPRESS")
                page.get_by_label("物流公司（快递必填）").fill("测试快递")
                page.get_by_label("运单号（快递必填）").fill(tracking)
                page.get_by_role("button", name="确认发货 / 交付", exact=True).click()
                if tracking == "FIRST-PARCEL":
                    expect(page.get_by_text("部分已发货", exact=True)).to_be_visible()
                    expect(page.get_by_role("link", name="取消订单", exact=True)).to_have_count(0)
            expect(page.get_by_text("测试快递 · FIRST-PARCEL", exact=True)).to_be_visible()
            expect(page.get_by_text("测试快递 · SECOND-PARCEL", exact=True)).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            screenshots = Path("artifacts/browser")
            screenshots.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshots / "partial-shipping-mobile.png"), full_page=True)
            page.get_by_role("link", name="确认履约完成", exact=True).click()
            page.get_by_role("button", name="确认履约完成", exact=True).click()
            expect(page.get_by_text("已完成交付", exact=True)).to_have_count(2)
            browser.close()
        order.refresh_from_db()
        assert order.status == "COMPLETED"
        assert order.cost_fen == 21000

    def test_stock_to_completed_sale_with_partial_receipts(self):
        call_command("bootstrap")
        User.objects.create_user("sales-owner", password="sales-browser-only", role=User.Role.ADMIN)
        screenshots = Path("artifacts/browser")
        screenshots.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                channel=os.environ.get("PLAYWRIGHT_CHANNEL") or None
            )
            page = browser.new_page(viewport={"width": 1365, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(self.live_server_url)
            page.get_by_label("用户名").fill("sales-owner")
            page.get_by_label("密码").fill("sales-browser-only")
            page.get_by_role("button", name="登录", exact=True).click()
            page.get_by_role("link", name="库存", exact=True).click()
            page.get_by_role("link", name="新增商品", exact=True).click()
            page.screenshot(path=str(screenshots / "product-form-desktop.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=str(screenshots / "product-form-mobile.png"), full_page=True)
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.set_viewport_size({"width": 1365, "height": 1000})
            page.get_by_label("商品名称", exact=True).fill("27 寸显示器")
            expect(page.get_by_label("成色（可自己写）", exact=True)).to_be_visible()
            page.get_by_label("成色（可自己写）", exact=True).fill("99 新")
            page.get_by_label("货况说明", exact=True).fill(
                "99 新，无配件，右上角翘边，黑底轻微漏光。"
            )
            page.get_by_text("更多描述（选填）", exact=True).click()
            page.get_by_label("内部备注", exact=True).fill("这段进货备注不进入成交说明")
            page.get_by_label("库存数量", exact=True).fill("2")
            page.get_by_label("单件进货成本（元）").fill("105.00")
            expect(page.locator('[name="unit_freight"]')).to_have_count(0)
            page.get_by_role("button", name="保存商品和库存", exact=True).click()
            expect(page.get_by_role("heading", name="首次录入")).to_be_visible()
            page.screenshot(path=str(screenshots / "catalog-desktop.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.screenshot(path=str(screenshots / "catalog-mobile.png"), full_page=True)
            page.get_by_text("销售", exact=True).click()
            page.get_by_role("link", name="销售订单", exact=True).click()
            page.get_by_role("link", name="新增订单", exact=True).click()
            expect(page.get_by_role("heading", name="这次卖什么商品？")).to_be_visible()
            page.screenshot(path=str(screenshots / "order-choose-mobile.png"), full_page=True)
            page.get_by_role("link", name="选择此商品 →", exact=True).click()
            expect(page.get_by_role("radio", name="首次录入", exact=False)).to_be_checked()
            page.get_by_label("客户称呼/临时标识").fill("测试熟客")
            page.get_by_label("销售渠道", exact=True).select_option(label="微信")
            page.get_by_label("成交单价（元）").fill("200.00")
            expect(page.locator("#sale-total")).to_have_text("¥200.00")
            page.screenshot(path=str(screenshots / "order-entry-mobile.png"), full_page=True)
            page.get_by_role("button", name="保存订单，下一步核对", exact=True).click()
            expect(page.get_by_text("这段进货备注不进入成交说明")).to_have_count(0)
            page.get_by_role("link", name="确认订单", exact=True).click()
            page.get_by_role("button", name="确认订单 / 补锁库存", exact=True).click()
            page.get_by_role("link", name="发货 / 交付", exact=True).click()
            page.get_by_label("交付方式", exact=True).select_option("HANDOVER")
            page.get_by_label("实际发货运费、包装等费用合计（元）").fill("10.00")
            page.get_by_role("button", name="确认发货 / 交付", exact=True).click()
            for amount in ("50.00", "150.00"):
                page.get_by_role("link", name="登记实际收款", exact=True).click()
                page.get_by_label("实际发生金额（元）").fill(amount)
                page.get_by_label("到账依据/原因").fill("已核对微信到账")
                page.get_by_role("button", name="保存", exact=True).click()
                if amount == "50.00":
                    expect(page.get_by_text("待确认", exact=True)).to_be_visible()
            expect(page.get_by_text("¥85.00", exact=True)).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.screenshot(path=str(screenshots / "order-mobile.png"), full_page=True)
            page.set_viewport_size({"width": 1365, "height": 1000})
            page.screenshot(path=str(screenshots / "order-desktop.png"), full_page=True)
            assert not errors
            browser.close()
        order = SalesOrder.objects.get()
        assert order.status == "COMPLETED"
        assert order.realized_profit_fen == 8500
        assert InventoryBalance.objects.get().available_qty == 1
