import os
import uuid
from pathlib import Path

import pytest
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.management import call_command
from playwright.sync_api import expect, sync_playwright

from app.accounts.models import User
from app.catalog.services import save_product
from app.inventory.models import InventoryBalance
from app.procurement.models import Purchase, PurchaseReceipt
from tests.test_shortage_procurement import shortage


@pytest.mark.browser
class TestProcurementBrowser(StaticLiveServerTestCase):
    def test_order_shortage_to_procurement_allocation(self):
        call_command("bootstrap")
        actor = User.objects.create_user(
            "shortage-owner", password="shortage-browser-only", role=User.Role.ADMIN
        )
        order, supplier = shortage(actor)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                channel=os.environ.get("PLAYWRIGHT_CHANNEL") or None
            )
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.goto(self.live_server_url)
            page.get_by_label("用户名").fill("shortage-owner")
            page.get_by_label("密码").fill("shortage-browser-only")
            page.get_by_role("button", name="登录", exact=True).click()
            page.goto(f"{self.live_server_url}/orders/{order.pk}/")
            page.get_by_role("link", name="为缺货采购", exact=True).click()
            expect(page.get_by_label("采购数量", exact=True)).to_have_value("2")
            page.get_by_label("供应商", exact=True).select_option(supplier)
            page.get_by_label("单件进货成本（元）").fill("80")
            page.get_by_role("button", name="保存采购单").click()
            page.get_by_role("link", name="确认下单", exact=True).click()
            page.get_by_label("操作说明").fill("已向供应商下单")
            page.get_by_role("button", name="确认已向供应商下单").click()
            page.get_by_role("link", name="登记实际收到", exact=True).click()
            page.get_by_label("本次数量", exact=True).fill("2")
            page.get_by_label("实际依据 / 操作说明").fill("实际收到，待验收")
            if page.locator('[name="dispatch"] option').count() > 1:
                page.get_by_label("来源发运批次", exact=False).select_option(index=1)
            page.get_by_role("button", name="登记实际收到", exact=True).click()
            page.get_by_role("link", name="验收这次到货", exact=True).click()
            page.get_by_label("本次数量", exact=True).fill("2")
            page.get_by_label("实际依据 / 操作说明").fill("已核对实际货况")
            page.get_by_label("货况说明", exact=True).fill("无配件，轻微漏光")
            page.get_by_label("成色（可自己写）").fill("95 新")
            page.get_by_role("button", name="验收入库", exact=True).click()
            page.get_by_role("link", name="核对货况，为订单备货").click()
            expect(page.get_by_role("heading", name="开单时的货况")).to_be_visible()
            expect(page.get_by_role("heading", name="本次到货的实际货况")).to_be_visible()
            page.get_by_label("已核对实际货况，确认这组货可用于此订单").check()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            screenshots = Path("artifacts/browser")
            screenshots.mkdir(parents=True, exist_ok=True)
            page.screenshot(
                path=str(screenshots / "shortage-allocation-mobile.png"), full_page=True
            )
            page.get_by_role("button", name="确认货况并锁定库存").click()
            expect(page.get_by_text("当前锁定 3 件 · 已出库 0 件", exact=True)).to_be_visible()
            expect(page.get_by_role("link", name="为缺货采购", exact=True)).to_have_count(0)
            browser.close()
        assert order.items.get().shortage_qty == 0

    def test_supplier_quote_purchase_receive_and_return(self):
        actor = User.objects.create_user(
            "buyer-owner", password="purchase-browser-only", role=User.Role.ADMIN
        )
        save_product(actor=actor, submission_key=uuid.uuid4(), name="采购测试显示器")
        screenshots = Path("artifacts/browser")
        screenshots.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                channel=os.environ.get("PLAYWRIGHT_CHANNEL") or None
            )
            page = browser.new_page(viewport={"width": 390, "height": 844})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(self.live_server_url)
            page.get_by_label("用户名").fill("buyer-owner")
            page.get_by_label("密码").fill("purchase-browser-only")
            page.get_by_role("button", name="登录", exact=True).click()
            page.goto(self.live_server_url + "/purchases/")
            page.get_by_role("link", name="供应商与报价", exact=True).click()
            page.get_by_role("link", name="新增供应商", exact=True).click()
            page.get_by_label("供应商名称").fill("常用拿货人")
            page.get_by_role("button", name="保存", exact=True).click()
            page.get_by_role("link", name="记录新报价").click()
            page.get_by_label("商品", exact=True).select_option(index=1)
            page.get_by_label("货况说明", exact=True).fill("无配件，轻微漏光")
            page.get_by_label("成色（可自己写）").fill("99 新")
            page.get_by_label("单件进货成本（元）").fill("105.00")
            page.get_by_role("button", name="保存", exact=True).click()
            page.get_by_role("link", name="按这条报价采购").click()
            expect(page.get_by_label("单件进货成本（元）")).to_have_value("105")
            page.get_by_label("采购数量", exact=True).fill("3")
            page.get_by_role("button", name="保存采购单").click()
            expect(page.get_by_text("待下单", exact=True)).to_be_visible()
            page.get_by_role("link", name="确认下单", exact=True).click()
            page.get_by_label("操作说明").fill("已联系供应商下单")
            page.get_by_role("button", name="确认已向供应商下单").click()
            page.get_by_role("link", name="登记采购付款").click()
            page.get_by_label("本次实际金额（元）").fill("315")
            page.get_by_label("操作说明").fill("已核对付款")
            page.get_by_role("button", name="登记采购付款").click()
            page.get_by_role("link", name="记录供应商发货").click()
            page.get_by_label("本次数量", exact=True).fill("2")
            page.get_by_label("实际依据 / 操作说明").fill("供应商已寄出两件")
            page.get_by_role("button", name="登记分批发运").click()
            page.get_by_role("link", name="登记实际收到", exact=True).click()
            page.get_by_label("本次数量", exact=True).fill("2")
            page.get_by_label("实际依据 / 操作说明").fill("实际收到，待验收")
            if page.locator('[name="dispatch"] option').count() > 1:
                page.get_by_label("来源发运批次", exact=False).select_option(index=1)
            page.get_by_role("button", name="登记实际收到", exact=True).click()
            page.get_by_role("link", name="验收这次到货", exact=True).click()
            page.get_by_label("本次数量", exact=True).fill("2")
            page.get_by_label("实际依据 / 操作说明").fill("已核对实际货况")
            page.get_by_label("货况说明", exact=True).fill("无配件，右上角翘边，已检测可用")
            expect(page.locator('[name="unit_freight"]')).to_have_count(0)
            page.screenshot(path=str(screenshots / "purchase-receive-mobile.png"), full_page=True)
            page.get_by_role("button", name="验收入库", exact=True).click()
            expect(page.get_by_text("部分到货", exact=True)).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.screenshot(path=str(screenshots / "purchase-mobile.png"), full_page=True)
            page.set_viewport_size({"width": 1365, "height": 1000})
            page.screenshot(path=str(screenshots / "purchase-desktop.png"), full_page=True)
            page.get_by_role("link", name="关闭剩余采购").click()
            page.get_by_label("操作说明").fill("剩余一件供应商召回，不再收货")
            page.get_by_role("button", name="关闭剩余采购").click()
            page.get_by_role("link", name="退回供应商", exact=True).click()
            page.get_by_label("本次退回数量").fill("1")
            page.get_by_label("操作说明").fill("已退回一件")
            page.get_by_role("button", name="登记退回供应商").click()
            page.get_by_role("link", name="登记供应商退款").click()
            page.get_by_label("本次实际金额（元）").fill("210")
            page.get_by_label("操作说明").fill("退货和取消的货款实际到账")
            page.get_by_role("button", name="登记供应商退款").click()
            assert not errors
            browser.close()
        purchase = Purchase.objects.get()
        assert (purchase.received_qty, purchase.cancelled_qty, purchase.returned_qty) == (2, 1, 1)
        assert purchase.net_paid_fen == purchase.total_fen == 10500
        assert PurchaseReceipt.objects.get().returned_qty == 1
        assert InventoryBalance.objects.get().available_qty == 1
