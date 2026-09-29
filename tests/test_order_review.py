from pathlib import Path
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from app.audit.models import AuditEvent
from app.common.business import BusinessError
from app.integrations.client import APIError
from app.integrations.services import store_order
from app.workbench.models import Trade
from app.workbench.review import fingerprint, revoke_review, save_review
from tests.test_workspace import connection as workspace_connection
from tests.test_workspace import payload

connection = workspace_connection
pytestmark = pytest.mark.django_db


def review_trade(connection):
    data = payload(order_status=23, refund_status=0, refund_amount=0, refund_time=1789298763)
    row = store_order(connection, data)
    return Trade.objects.get(platform=row), data


def confirm(trade, actor, **changes):
    return save_review(
        **{
            "trade": trade,
            "actor": actor,
            "expected": fingerprint(trade.platform),
            "result": "full",
            "amount": trade.paid_fen,
            "basis": "在闲鱼退款详情核实全额退款成功。",
            **changes,
        }
    )


def test_review_preserves_external_facts_and_survives_identical_sync(connection, admin_user):
    trade, data = review_trade(connection)
    result = confirm(trade, admin_user)
    assert result.status == "REFUNDED" and result.refunded_fen == data["pay_amount"]
    assert result.recovered_at is None and result.profit_fen is None
    assert result.platform.snapshot["refund_amount"] == 0
    confirm(result, admin_user)
    assert AuditEvent.objects.filter(action="workbench.review_recorded").count() == 1
    store_order(connection, {**data, "update_time": data["update_time"] + 1})
    result.refresh_from_db()
    assert result.status == "REFUNDED" and result.refunded_fen == data["pay_amount"]
    changed = {**data, "refund_amount": 100, "update_time": data["update_time"] + 2}
    store_order(connection, changed)
    result.refresh_from_db()
    assert result.status == "REVIEW" and result.refunded_fen == 100
    assert result.review_resolution["amount"] == data["pay_amount"]


def test_stale_confirmation_and_amount_mismatch_are_rejected(connection, admin_user):
    trade, data = review_trade(connection)
    old = fingerprint(trade.platform)
    with pytest.raises(BusinessError, match="实际金额"):
        confirm(trade, admin_user, amount=100)
    store_order(
        connection,
        {**data, "refund_time": data["refund_time"] + 1, "update_time": data["update_time"] + 1},
    )
    with pytest.raises(BusinessError, match="发生了变化"):
        confirm(trade, admin_user, expected=old)
    assert not AuditEvent.objects.filter(action="workbench.review_recorded").exists()


@pytest.mark.parametrize(
    "change",
    [
        {"refund_time": None},
        {"refund_amount": 100},
        {"refund_status": 1},
        {"order_status": 12},
        {"order_status": 999, "refund_status": 5},
    ],
)
def test_ambiguous_refunds_cannot_be_cleared(connection, admin_user, change):
    data = payload(order_status=23, refund_status=0, refund_amount=0, refund_time=1789298763, **{})
    data.update(change)
    row = store_order(connection, data)
    trade = Trade.objects.get(platform=row)
    with pytest.raises(BusinessError):
        confirm(trade, admin_user)


def test_record_unresolved_result_and_revoke(connection, admin_user):
    trade, data = review_trade(connection)
    result = confirm(
        trade, admin_user, result="pending", amount=100, basis="实际为部分退款，待处理。"
    )
    assert result.status == "REVIEW" and result.refunded_fen == 0
    assert result.review_resolution["amount"] == 100
    result = confirm(result, admin_user)
    assert result.status == "REFUNDED"
    result = revoke_review(trade=result, actor=admin_user, expected=fingerprint(result.platform))
    assert result.status == "REVIEW" and result.refunded_fen == 0
    assert not result.review_resolution and result.recovered_at is None
    assert AuditEvent.objects.filter(action="workbench.review_revoked").count() == 1


def test_review_web_permissions_errors_and_history(connection, admin_user, operator, client):
    trade, _ = review_trade(connection)
    url = reverse("wb-review", args=[trade.pk])
    assert client.get(url).status_code == 302
    client.force_login(operator)
    assert client.post(url, {"operation": "refresh"}).status_code == 403
    client.force_login(admin_user)
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(admin_user)
    assert strict.post(url, {"operation": "refresh"}).status_code == 403
    page = client.get(url)
    assert "不能据此认定没有退款" in page.content.decode()
    with patch("app.workbench.views.refresh_order", side_effect=APIError("模拟连接失败")):
        response = client.post(url, {"operation": "refresh"})
    assert response.status_code == 200 and "未全部成功" in response.content.decode()
    with (
        patch("app.workbench.views.refresh_order") as order,
        patch("app.workbench.views.refresh_refund") as refund,
    ):
        assert client.post(url, {"operation": "refresh"}).status_code == 302
    assert order.call_count == refund.call_count == 1
    response = client.post(
        url,
        {
            "operation": "save",
            "fingerprint": fingerprint(trade.platform),
            "result": "full",
            "amount": "200.00",
            "basis": "已核实退款详情",
            "confirmed": "on",
        },
        follow=True,
    )
    assert response.status_code == 200
    assert (
        "核对已完成" in response.content.decode() and "已核实退款详情" in response.content.decode()
    )
    trade.refresh_from_db()
    assert trade.status == "REFUNDED"
    detail = client.get(reverse("wb-detail", args=[trade.pk])).content.decode()
    assert "店主人工核实" in detail
    assert (
        client.post(
            url, {"operation": "revoke", "fingerprint": fingerprint(trade.platform)}, follow=True
        ).status_code
        == 200
    )
    trade.refresh_from_db()
    assert trade.status == "REVIEW"


def test_confirmation_requires_checkbox_and_basis(connection, admin_user, client):
    trade, _ = review_trade(connection)
    client.force_login(admin_user)
    response = client.post(
        reverse("wb-review", args=[trade.pk]),
        {
            "operation": "save",
            "fingerprint": fingerprint(trade.platform),
            "result": "full",
            "amount": "200.00",
        },
    )
    assert response.status_code == 200
    trade.refresh_from_db()
    assert trade.status == "REVIEW" and not trade.review_resolution


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_review_browser_flow(connection, admin_user, client, live_server):
    from playwright.sync_api import expect, sync_playwright

    trade, _ = review_trade(connection)
    client.force_login(admin_user)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
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
        page.goto(live_server.url + reverse("wb-orders") + "?status=REVIEW")
        page.get_by_role("link", name="去核对", exact=True).click()
        expect(page.get_by_role("heading", name="核对订单", exact=True)).to_be_visible()
        output = Path("artifacts/browser")
        output.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(output / "order-review-desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(output / "order-review-mobile.png"), full_page=True)
        page.locator("#id_result").select_option("full")
        page.locator("#id_amount").fill("200.00")
        page.locator("#id_basis").fill("演示订单：已在平台退款详情核实全额退款成功。")
        page.locator("#id_confirmed").check()
        page.get_by_role("button", name="保存核对结果", exact=True).click()
        expect(page.get_by_text("核对已完成，订单已归入已退款。", exact=True)).to_be_visible()
        expect(page.get_by_text("记录订单核对结果", exact=True)).to_be_visible()
        page.get_by_role("button", name="撤销人工核对结果").click()
        expect(page.get_by_text("撤销订单核对结果", exact=True)).to_be_visible()
        browser.close()
    trade.refresh_from_db()
    assert trade.status == "REVIEW" and trade.recovered_at is None
