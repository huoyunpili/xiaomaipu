import hashlib
import os
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright

from app.accounts.models import User
from app.audit.models import AuditEvent
from app.shops.models import Shop
from app.workbench.models import ExportImage, ProductCost, Trade
from app.workbench.services import create_batches
from tests.test_workspace_hardening import png_bytes


@pytest.mark.browser
class TestWorkspaceBrowser(StaticLiveServerTestCase):
    def test_single_supplier_popup_repeated_open_and_touch(self):
        call_command("bootstrap")
        User.objects.create_user("popup-owner", password="popup-test-only", role="ADMIN")
        shop = Shop.objects.get(is_active=True)
        ProductCost.objects.create(shop=shop, key="one", title="单供应商", supplier="李龙")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel=os.environ.get("PLAYWRIGHT_CHANNEL") or None)
            page = browser.new_page(has_touch=True, viewport={"width": 390, "height": 700})
            page.goto(self.live_server_url)
            page.get_by_label("用户名").fill("popup-owner")
            page.get_by_label("密码").fill("popup-test-only")
            page.get_by_role("button", name="登录", exact=True).click()
            expect(page.get_by_role("heading", name="我的小卖铺")).to_be_visible()
            page.goto(self.live_server_url + reverse("wb-costs"))
            supplier = page.get_by_label("默认供应商", exact=True)
            toggle = page.get_by_role("button", name="展开供应商列表")
            popup = page.get_by_role("listbox", name="供应商选项")
            for _ in range(3):
                toggle.click()
                expect(popup).to_be_visible()
                expect(popup.get_by_role("option")).to_have_count(1)
                toggle.click()
                expect(popup).not_to_be_visible()
            toggle.tap()
            expect(popup).to_be_visible()
            popup.get_by_role("option").tap()
            expect(popup).not_to_be_visible()
            expect(supplier).to_have_value("李龙")
            supplier.click()
            expect(popup).to_be_visible()
            page.mouse.wheel(0, 120)
            bounds = popup.bounding_box()
            assert bounds and 0 <= bounds["y"] < 700
            supplier.press("Tab")
            expect(popup).not_to_be_visible()
            stylesheet = page.locator('link[href*="workbench.css"]').get_attribute("href")
            assert stylesheet and "?v=" in stylesheet
            old_style = (
                Path("app/static/workbench.css")
                .read_text(encoding="utf-8")
                .split(".wb-supplier-combobox")[0]
            )
            page.route(
                "**/static/workbench.css*",
                lambda route: route.fulfill(status=200, content_type="text/css", body=old_style),
            )
            page.reload()
            expect(supplier).to_have_attribute("list", "saved-suppliers")
            expect(page.locator(".wb-supplier-toggle")).to_have_count(0)
            page.unroute("**/static/workbench.css*")
            page.reload()
            expect(toggle).to_be_visible()
            assert toggle.evaluate("el => getComputedStyle(el).position") == "absolute"
            toggle.click()
            expect(popup).to_be_visible()
            assert popup.evaluate("el => getComputedStyle(el).position") == "fixed"
            browser.close()

    def test_saved_supplier_selection_and_contact_fill(self):
        media = self.enterContext(TemporaryDirectory())
        self.enterContext(override_settings(PRIVATE_MEDIA_ROOT=media))
        call_command("bootstrap")
        User.objects.create_user("supplier-owner", password="supplier-browser-only", role="ADMIN")
        shop = Shop.objects.get(is_active=True)
        ProductCost.objects.create(
            shop=shop,
            key="saved",
            title="已有配置",
            supplier="李龙",
            supplier_wechat="枫",
            unit_fen=1000,
        )
        full_title = "新增配置 " + "完整商品型号及用途说明" * 10
        target = ProductCost.objects.create(
            shop=shop, key="new", title=full_title, spec="太空银 · 64GB"
        )
        ProductCost.objects.create(
            shop=shop,
            key="another",
            title="另一个商品配置",
            supplier="王敏",
            supplier_wechat="工厂二号",
        )
        picture = "https://img.alicdn.com/cost-card-test.png"
        example = Trade.objects.create(shop=shop, product=target, number="COST-CARD", image=picture)
        cache = Path(media) / "product-images"
        cache.mkdir()
        (cache / hashlib.sha256(picture.encode()).hexdigest()).write_bytes(png_bytes())
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel=os.environ.get("PLAYWRIGHT_CHANNEL") or None)
            page = browser.new_page()
            page.goto(self.live_server_url)
            page.get_by_label("用户名").fill("supplier-owner")
            page.get_by_label("密码").fill("supplier-browser-only")
            page.get_by_role("button", name="登录", exact=True).click()
            expect(page.get_by_role("heading", name="我的小卖铺")).to_be_visible()
            page.goto(self.live_server_url + reverse("wb-costs"))
            expect(page.locator('#saved-suppliers option[value="李龙"]')).to_have_count(1)
            form = page.locator("form.wb-panel").filter(has_text="新增配置")
            for width in (390, 1365):
                page.set_viewport_size({"width": width, "height": 900})
                expect(form.locator("h2")).to_have_text(full_title)
                expect(form.locator(".wb-product-spec")).to_have_text("太空银 · 64GB")
                form.locator("img").evaluate("img => img.decode()")
                assert form.locator("img").evaluate("img => img.naturalWidth") > 0
                expect(form.get_by_role("link", name="查看关联订单 ↗")).to_have_attribute(
                    "href", reverse("wb-detail", args=[example.pk])
                )
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert form.locator("h2").evaluate("el => el.scrollWidth <= el.clientWidth")
                output = Path("artifacts/browser/workspace")
                output.mkdir(parents=True, exist_ok=True)
                page.screenshot(
                    path=str(output / f"cost-identification-{width}.png"), full_page=True
                )
            supplier = form.get_by_label("默认供应商", exact=True)
            wechat = form.get_by_label("供应商微信备注名称")
            supplier.click()
            expect(supplier).to_have_attribute("role", "combobox")
            expect(supplier).not_to_have_attribute("list", "saved-suppliers")
            popup = page.get_by_role("listbox", name="供应商选项")
            expect(popup).to_be_visible()
            supplier.fill("工厂二号")
            supplier.press("ArrowDown")
            supplier.press("Enter")
            expect(supplier).to_have_value("王敏")
            expect(wechat).to_have_value("工厂二号")
            expect(popup).not_to_be_visible()
            for width in (390, 1365):
                page.set_viewport_size({"width": width, "height": 900})
                form.get_by_role("button", name="展开供应商列表").click()
                expect(popup).to_be_visible()
                bounds = popup.bounding_box()
                assert bounds and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width
                assert bounds["y"] >= 0 and bounds["y"] + bounds["height"] <= 900
                page.screenshot(path=str(output / f"supplier-dropdown-{width}.png"))
                supplier.press("Escape")
                expect(supplier).to_have_value("王敏")
            form.get_by_role("button", name="展开供应商列表").click()
            popup.get_by_role("option").filter(has_text="李龙").click()
            expect(supplier).to_have_value("李龙")
            expect(wechat).to_have_value("枫")
            supplier.fill("未登记供应商")
            expect(popup.get_by_role("option")).to_contain_text("使用新供应商")
            supplier.press("Escape")
            expect(supplier).to_have_value("未登记供应商")
            supplier.fill("李龙")
            expect(wechat).to_have_value("枫")
            supplier.fill("新供应商")
            expect(wechat).to_have_value("")
            supplier.fill("李龙")
            wechat.fill("本商品自定义备注")
            form.get_by_label("单件成本（元）").fill("60")
            other_form = page.locator("form.wb-panel").filter(has_text="已有配置")
            expect(other_form.get_by_role("button", name="已保存", exact=True)).to_be_disabled()
            other_form.get_by_label("默认发货备注").fill("其他商品尚未保存")
            button = form.locator("[data-save-product]")
            button.scroll_into_view_if_needed()
            scroll_before = page.evaluate("scrollY")
            held_requests = []
            page.route("**/workspace/costs/", lambda route: held_requests.append(route))
            button.click()
            expect(button).to_have_text("保存中…")
            expect(button).to_be_disabled()
            form.get_by_label("默认发货备注").fill("保存期间继续修改")
            assert len(held_requests) == 1
            held_requests[0].continue_()
            page.unroute("**/workspace/costs/")
            expect(button).to_have_text("保存")
            expect(button).to_be_enabled()
            expect(form.get_by_label("默认发货备注")).to_have_value("保存期间继续修改")
            assert abs(page.evaluate("scrollY") - scroll_before) < 3
            page.route("**/workspace/costs/", lambda route: route.abort())
            button.click()
            expect(form.locator(".wb-save-feedback")).to_contain_text("当前输入已保留")
            expect(button).to_be_enabled()
            page.unroute("**/workspace/costs/")
            button.click()
            expect(button).to_have_text("已保存")
            expect(button).to_be_disabled()
            expect(other_form.get_by_label("默认发货备注")).to_have_value("其他商品尚未保存")
            expect(other_form.get_by_role("button", name="保存", exact=True)).to_be_enabled()
            wechat.fill("再次编辑")
            expect(button).to_have_text("保存")
            expect(button).to_be_enabled()
            wechat.fill("本商品自定义备注")
            expect(button).to_have_text("已保存")
            expect(button).to_be_disabled()
            expect(form.get_by_label("供应商微信备注名称")).to_have_value("本商品自定义备注")
            browser.close()
        target.refresh_from_db()
        assert target.supplier == "李龙" and target.supplier_wechat == "本商品自定义备注"
        assert target.shipping_note == "保存期间继续修改"

    def test_long_shipping_list_text_without_images(self):
        media = self.enterContext(TemporaryDirectory())
        self.enterContext(override_settings(PRIVATE_MEDIA_ROOT=media))
        call_command("bootstrap")
        owner = User.objects.create_user(
            "pages-owner", password="pages-browser-only", role=User.Role.ADMIN
        )
        shop = Shop.objects.get(is_active=True)
        trades = [
            Trade.objects.create(
                shop=shop,
                number=f"PAGES-{index:03}",
                status="SHIPPING",
                title="分页测试商品",
                spec="加长规格" * 40,
                quantity=1,
                paid_fen=10000,
                supplier="分页测试供应商",
                receiver="测试收件人",
                phone="13000000000",
                address="测试地址" * 200,
                note="请核对包装" * 200,
                paid_at=timezone.now(),
            )
            for index in range(12)
        ]
        batch = create_batches(trades, "shipping", owner)[0]
        snapshot = batch.snapshot
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel=os.environ.get("PLAYWRIGHT_CHANNEL") or None)
            page = browser.new_page()
            page.goto(self.live_server_url)
            page.get_by_label("用户名").fill("pages-owner")
            page.get_by_label("密码").fill("pages-browser-only")
            page.get_by_role("button", name="登录", exact=True).click()
            expect(page.get_by_role("heading", name="我的小卖铺")).to_be_visible()
            page.goto(self.live_server_url + reverse("wb-batch", args=[batch.pk]))
            rendered = page.locator("#shipping-text").input_value()
            assert "分页测试商品" in rendered
            for trade in trades:
                assert trade.number in rendered
                assert trade.address in rendered
            expect(page.locator("#sheet-images, #sheet-controls, #sheet-status")).to_have_count(0)
            expect(page.locator('script[src*="workbench-export.js"]')).to_have_count(0)
            with page.expect_download() as download:
                page.get_by_role("link", name="下载 TXT").click()
            downloaded = Path(media) / "shipping.txt"
            download.value.save_as(downloaded)
            assert downloaded.read_text(encoding="utf-8-sig") == rendered
            browser.close()
        assert not ExportImage.objects.filter(batch=batch).exists()
        batch.refresh_from_db()
        assert batch.snapshot == snapshot

    def test_workspace_pages_and_local_png(self):
        media = self.enterContext(TemporaryDirectory())
        self.enterContext(override_settings(PRIVATE_MEDIA_ROOT=media))
        call_command("bootstrap")
        owner = User.objects.create_user(
            "workspace-owner", password="workspace-browser-only", role=User.Role.ADMIN
        )
        trade = Trade.objects.create(
            product=ProductCost.objects.create(
                shop=Shop.objects.get(is_active=True),
                key="browser-product",
                title="测试型号",
                spec="黑色 大号",
            ),
            shop=Shop.objects.get(is_active=True),
            number="100000001",
            status="SHIPPING",
            title="测试型号",
            spec="黑色 大号 加长规格",
            quantity=2,
            paid_fen=20000,
            unit_cost_fen=6000,
            supplier="测试供应商",
            receiver="测试收件人",
            phone="13000000000",
            address="测试省测试市测试区" + "长地址测试" * 25,
            ordered_at=timezone.now(),
            paid_at=timezone.now(),
        )
        batch = create_batches([trade], "shipping", owner)[0]
        refund = Trade.objects.create(
            shop=trade.shop,
            number="100000003",
            status="REFUNDED",
            title="退款测试商品",
            spec="黑色 大号",
            quantity=2,
            paid_fen=20000,
            unit_cost_fen=6000,
            supplier=trade.supplier,
            receiver=trade.receiver,
            phone=trade.phone,
            refund_applied_at=timezone.now() - timedelta(days=2),
            refund_type=2,
            refunded_at=timezone.now(),
            refunded_fen=20000,
            refund_waybill="RETURN-TEST",
        )
        refund_batch = create_batches([refund], "refund", owner)[0]
        Trade.objects.create(
            shop=trade.shop,
            number="100000002",
            status="COMPLETED",
            title="已成交测试商品",
            spec="蓝色 大号",
            quantity=2,
            paid_fen=20000,
            unit_cost_fen=6000,
            paid_at=timezone.now(),
            completed_at=timezone.now(),
        )
        output = Path("artifacts/browser/workspace")
        output.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel=os.environ.get("PLAYWRIGHT_CHANNEL") or None)
            page = browser.new_page(viewport={"width": 1365, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(self.live_server_url)
            page.get_by_label("用户名").fill("workspace-owner")
            page.get_by_label("密码").fill("workspace-browser-only")
            page.get_by_role("button", name="登录", exact=True).click()
            expect(page.get_by_role("heading", name="我的小卖铺")).to_be_visible()
            page.goto(self.live_server_url + reverse("wb-costs"))
            page.get_by_label("单件成本（元）").fill("60")
            page.get_by_label("默认供应商", exact=True).fill("测试供应商")
            page.get_by_label("供应商微信备注名称").fill("测试工厂微信")
            page.get_by_label("默认发货备注").fill("加固包装，不放价格单")
            page.get_by_role("button", name="保存", exact=True).click()
            expect(page.get_by_role("button", name="已保存", exact=True)).to_be_disabled()
            expect(page.get_by_label("供应商微信备注名称")).to_have_value("测试工厂微信")
            expect(page.get_by_label("默认发货备注")).to_have_value("加固包装，不放价格单")
            for width, height in ((1365, 900), (390, 844)):
                page.set_viewport_size({"width": width, "height": height})
                for name in (
                    "dashboard",
                    "wb-shipping",
                    "wb-pending",
                    "wb-refunds",
                    "wb-orders",
                    "wb-profits",
                    "wb-settings",
                    "wb-costs",
                ):
                    page.goto(self.live_server_url + reverse(name))
                    expect(page.locator("h1")).to_be_visible()
                    current_nav = page.locator('.primary-nav a[aria-current="page"]')
                    expect(current_nav).to_have_count(1)
                    sidebar = page.locator(".workspace-sidebar").bounding_box()
                    content = page.locator("main").bounding_box()
                    assert sidebar and content
                    if width > 900:
                        assert content["x"] >= sidebar["x"] + sidebar["width"]
                    else:
                        assert content["y"] >= sidebar["y"] + sidebar["height"]
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), name
                    page.screenshot(path=str(output / f"{name}-{width}.png"), full_page=True)
                page.goto(self.live_server_url + reverse("wb-profits"))
                expect(page.locator(".wb-profit-metrics .wb-metric")).to_have_count(6)
                expect(page.locator("tfoot")).to_contain_text("76.80")
                page.locator('td[data-label="客户实付"] summary').click()
                expect(page.get_by_text("平台实付金额 pay_amount", exact=True)).to_be_visible()
                page.get_by_label("排序", exact=False).select_option("profit_high")
                page.get_by_role("button", name="查询", exact=True).click()
                expect(page.locator("tfoot")).to_contain_text("76.80")
                with page.expect_download() as csv_download:
                    page.get_by_role("link", name="导出当前统计明细").click()
                csv_path = output / f"profit-{width}.csv"
                csv_download.value.save_as(csv_path)
                assert "7680" in csv_path.read_text(encoding="utf-8-sig")
            page.goto(self.live_server_url + reverse("wb-batch", args=[batch.pk]))
            expect(page.get_by_role("button", name="复制整份清单")).to_be_visible()
            expect(page.get_by_role("link", name="下载 TXT")).to_be_visible()
            expect(page.locator("#sheet-images, #sheet-controls")).to_have_count(0)
            page.goto(self.live_server_url + reverse("wb-batch", args=[refund_batch.pk]))
            refund_link = page.get_by_role("link", name="下载 PNG · 第 1 张")
            expect(refund_link).to_be_visible()
            with page.expect_download() as refund_download:
                refund_link.click()
            refund_download.value.save_as(output / "refund.png")
            assert (output / "refund.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
            page.goto(self.live_server_url + reverse("wb-detail", args=[refund.pk]))
            page.get_by_label("确认供应商货款已收回").check()
            page.get_by_role("button", name="保存", exact=True).click()
            expect(page.get_by_label("确认供应商货款已收回")).to_be_checked()
            page.goto(self.live_server_url + reverse("wb-refunds"))
            expect(page.get_by_text("当前分类没有订单", exact=True)).to_be_visible()
            page.get_by_role("link", name="查看全部售后（含已追回）").click()
            expect(page.get_by_text("已收回", exact=False)).to_be_visible()
            page.screenshot(path=str(output / "recovered-history-390.png"), full_page=True)
            page.goto(self.live_server_url + reverse("wb-detail", args=[refund.pk]))
            page.get_by_label("确认供应商货款已收回").uncheck()
            page.get_by_role("button", name="保存", exact=True).click()
            expect(page.get_by_label("确认供应商货款已收回")).not_to_be_checked()
            assert not errors
            browser.close()
        trade.refresh_from_db()
        refund.refresh_from_db()
        assert trade.supplier_wechat == "测试工厂微信"
        assert trade.default_shipping_note == "加固包装，不放价格单"
        assert refund.recovered_at is None and refund.recovery_fen == 12000
        events = list(
            AuditEvent.objects.filter(
                action="workbench.order_edited", object_id=str(refund.pk)
            ).order_by("occurred_at")
        )
        assert [
            (event.details["before"]["recovered"], event.details["recovered"]) for event in events
        ] == [(False, True), (True, False)]
