import os
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.cache import cache
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright

from app.accounts.models import User
from app.integrations.models import Connection
from app.integrations.services import store_order
from app.shops.models import Shop
from app.workbench.models import SupplierDispatch, SupplierVideo, Trade
from app.workbench.services import create_batches
from app.workbench.supplier_service import ensure_access
from tests.test_workspace import payload


@pytest.mark.browser
class SupplierBrowserTests(StaticLiveServerTestCase):
    def test_mobile_partial_upload_retry_and_shipping(self):
        media = self.enterContext(TemporaryDirectory())
        self.enterContext(override_settings(PRIVATE_MEDIA_ROOT=media))
        self.enterContext(patch("app.workbench.supplier_service.enqueue_dispatch"))
        cache.clear()
        cache.set(
            "supplier_carriers",
            [
                {"code": "shunfeng", "name": "顺丰速运"},
                {"code": "yuantong", "name": "圆通速递"},
                {"code": "zhongtong", "name": "中通快递"},
                {"code": "shentong", "name": "申通快递"},
                {"code": "jd", "name": "京东快递"},
                {"code": "jtexpress", "name": "极兔速递"},
                {"code": "debangkuaidi", "name": "德邦快递"},
                {"code": "yunda", "name": "韵达快递"},
                {"code": "ems", "name": "EMS"},
            ],
        )
        call_command("bootstrap")
        owner = User.objects.create_user("supplier-test-owner", role=User.Role.ADMIN)
        shop = Shop.objects.get(is_active=True)
        connection = Connection.objects.create(
            shop=shop,
            actor=owner,
            seller_id="browser-test",
            sync_start_at=timezone.now() - timedelta(days=30),
        )
        trades = []
        for number in ("991111001", "991111002"):
            row = store_order(connection, payload(number=number))
            trade = Trade.objects.get(platform=row)
            trade.supplier, trade.image = "李龙", ""
            trade.save()
            trades.append(trade)
        access = ensure_access(create_batches(trades, "shipping", owner)[0])
        output = Path("artifacts/browser/supplier")
        output.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel=os.environ.get("PLAYWRIGHT_CHANNEL") or None)
            page = browser.new_page(viewport={"width": 390, "height": 844})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(self.live_server_url + reverse("supplier-portal", args=[access.token]))
            expect(page.get_by_role("heading", name="发货清单", exact=True)).to_be_visible()
            expect(page).to_have_title("发货回传")
            expect(page.locator("header")).not_to_contain_text("李龙")
            recipient = page.locator("[data-recipient-text]").first
            expect(recipient).to_contain_text("测试客户")
            expect(recipient).to_contain_text("测试路123号")
            page.context.grant_permissions(["clipboard-read", "clipboard-write"])
            page.locator("[data-copy-recipient]").first.click()
            expect(page.locator("[data-copy-result]").first).to_have_text("收货信息已复制")
            assert "测试路123号" in page.evaluate("navigator.clipboard.readText()")
            card = page.locator("[data-order]").first
            file = {
                "name": "packing.mp4",
                "mimeType": "video/mp4",
                "buffer": b"\x00\x00\x00\x18ftypisom" + b"0" * 1024,
            }
            card.locator("[type=file]").set_input_files(file)
            page.route(
                "**/video/",
                lambda route: route.fulfill(
                    status=503,
                    content_type="application/json",
                    body='{"error":"temporary failure"}',
                ),
            )
            card.get_by_role("button", name="保存所选视频").click()
            expect(card.locator("[data-video] .result")).to_contain_text("temporary failure")
            page.unroute("**/video/")
            card.get_by_role("button", name="保存所选视频").click()
            expect(card.locator("[data-video] .result")).to_contain_text("所选视频均已保存")
            page.reload()
            expect(card.locator("[data-videos]")).to_contain_text("packing.mp4")
            card.locator("[type=file]").set_input_files(file)
            card.get_by_role("button", name="保存所选视频").click()
            expect(card.locator("[data-video] .result")).to_contain_text("所选视频均已保存")
            expect(card.locator("[data-videos] li")).to_have_count(1)
            for number, expected in (
                (" sf1234567890123 ", "shunfeng"),
                ("YT1234567890123", "yuantong"),
                ("79012345678901", "zhongtong"),
                ("79112345678901", "zhongtong"),
                ("773123456789012", "shentong"),
                ("JD123456789012", "jd"),
                ("JDL1234567890123", "jd"),
                ("jt1234567890123", "jtexpress"),
                ("DPK123456789012", "debangkuaidi"),
                ("YD123456789012", "yunda"),
                ("EA123456789CN", "ems"),
                ("EA123456789US", ""),
                ("RR123456789CN", ""),
                ("DPK12345678901", ""),
                ("JT12345678901234", ""),
                ("JDABC123456789012", ""),
                ("123456789012", ""),
                ("123456789012345", ""),
                ("SF123456789012", ""),
            ):
                card.get_by_label("快递单号").fill(number)
                expect(card.get_by_label("快递公司")).to_have_value(expected)
            card.get_by_label("快递单号").fill("YT1234567890123")
            card.get_by_label("快递公司").select_option("shentong")
            card.get_by_label("快递单号").blur()
            expect(card.get_by_label("快递公司")).to_have_value("shentong")
            # A unique match absent from the platform list must not be fabricated.
            card.get_by_label("快递公司").locator('option[value="yuantong"]').evaluate(
                "el=>el.remove()"
            )
            card.get_by_label("快递单号").fill("YT1234567890124")
            expect(card.get_by_label("快递公司")).to_have_value("")
            card.get_by_label("快递单号").fill("SF12345678")
            card.get_by_label("快递公司").select_option("shunfeng")
            submitted_controls = []

            def check_locked(route):
                submitted_controls.append(
                    card.get_by_label("快递单号").is_disabled()
                    and card.get_by_label("快递公司").is_disabled()
                )
                route.continue_()

            page.route("**/ship/", check_locked)
            card.get_by_role("button", name="保存单号并提交发货").click()
            expect(card.locator("[data-dispatch-state]")).to_contain_text("等待提交平台")
            assert submitted_controls == [True]
            expect(card.get_by_role("button", name="保存单号并提交发货")).to_be_disabled()
            page.reload()
            expect(card.get_by_label("快递单号")).to_have_value("SF12345678")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(output / "mobile.png"), full_page=True)
            page.set_viewport_size({"width": 1365, "height": 900})
            page.screenshot(path=str(output / "desktop.png"), full_page=True)
            assert not errors
            browser.close()
        assert SupplierVideo.objects.count() == 1
        assert sum(not t.supplier_videos.exists() for t in trades) == 1
        assert SupplierDispatch.objects.count() == 1
