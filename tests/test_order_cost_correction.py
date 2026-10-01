import pytest
from django.urls import reverse

from app.audit.models import AuditEvent
from app.integrations.services import store_order
from app.workbench.models import Trade
from app.workbench.services import update_product
from tests.test_workspace import connection, payload  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "old_cost,new_cost", [(None, 82500), (0, 82500), (82000, 82500), (82500, 0)]
)
def test_detail_cost_save_updates_profit_and_survives_sync(
    connection,  # noqa: F811
    admin_user,
    client,
    old_cost,
    new_cost,
):
    data = payload(pay_amount=83900, order_status=22)
    data["confirm_time"] = data["pay_time"]
    data["goods"]["quantity"] = 1
    row = store_order(connection, data)
    trade = Trade.objects.get(platform=row)
    update_product(trade.product, 82500, "", admin_user)
    Trade.objects.filter(pk=trade.pk).update(unit_cost_fen=old_cost)
    client.force_login(admin_user)
    response = client.post(
        reverse("wb-detail", args=[trade.pk]),
        {"correction": f"{new_cost / 100:.2f}", "reason": "测试核实本单实际进价"},
        follow=True,
    )
    assert response.redirect_chain == [(reverse("wb-detail", args=[trade.pk]), 302)]
    trade.refresh_from_db()
    expected = 83900 - new_cost - 1342
    assert trade.unit_cost_fen == new_cost and trade.profit_fen == expected
    assert response.context["trade"].profit_fen == expected
    # This is a one-shot correction input, not the display of the saved cost.
    assert response.context["form"]["correction"].value() is None
    audit = AuditEvent.objects.get(action="workbench.order_edited", object_id=str(trade.pk))
    assert audit.details["before"]["cost"] == old_cost
    assert audit.details["cost"] == new_cost and audit.details["reason"]
    for mode in ("expected", "actual"):
        result = client.get(reverse("wb-profits"), {"mode": mode, "range": "today"})
        assert result.context["summary"]["profit"] == expected
        assert result.context["summary"]["cost"] == new_cost
    data["update_time"] += 1
    store_order(connection, data)
    update_product(trade.product, 90000, "", admin_user)
    trade.refresh_from_db()
    assert trade.unit_cost_fen == new_cost and trade.profit_fen == expected


def test_detail_cost_without_reason_is_rejected_and_keeps_input(connection, admin_user, client):  # noqa: F811
    row = store_order(connection, payload())
    trade = Trade.objects.get(platform=row)
    Trade.objects.filter(pk=trade.pk).update(unit_cost_fen=0)
    client.force_login(admin_user)
    response = client.post(reverse("wb-detail", args=[trade.pk]), {"correction": "825"})
    assert response.status_code == 200
    assert "reason" in response.context["form"].errors
    assert "更正历史成本必须填写原因。" in response.content.decode()
    assert response.context["form"]["correction"].value() == "825"
    trade.refresh_from_db()
    assert trade.unit_cost_fen == 0
    assert not AuditEvent.objects.filter(action="workbench.order_edited").exists()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_browser_detail_cost_save(connection, admin_user, client, live_server):  # noqa: F811
    from playwright.sync_api import expect, sync_playwright

    data = payload(pay_amount=83900, order_status=22)
    data["confirm_time"] = data["pay_time"]
    data["goods"]["quantity"] = 1
    data["goods"]["images"] = []
    row = store_order(connection, data)
    trade = Trade.objects.get(platform=row)
    Trade.objects.filter(pk=trade.pk).update(unit_cost_fen=0)
    client.force_login(admin_user)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context()
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
        page.route("**/product-image/", lambda route: route.abort())
        page.goto(live_server.url + reverse("wb-detail", args=[trade.pk]))
        page.locator("#id_correction").fill("825")
        page.get_by_role("button", name="保存", exact=True).click()
        expect(page.get_by_text("更正历史成本必须填写原因。", exact=True)).to_be_visible()
        expect(page.get_by_text("¥825.58", exact=True)).to_be_visible()
        page.locator("#id_reason").fill("浏览器测试核对进价")
        page.get_by_role("button", name="保存", exact=True).click()
        expect(page.get_by_text("订单补充信息已保存。", exact=True)).to_be_visible()
        expect(page.locator("#id_correction")).to_have_value("")
        expect(page.get_by_text("¥0.58", exact=True)).to_be_visible()
        page.reload()
        expect(page.get_by_text("¥0.58", exact=True)).to_be_visible()
        page.goto(live_server.url + reverse("wb-profits") + "?mode=expected&range=today")
        expect(page.locator(".wb-profit-metrics")).to_contain_text("¥0.58")
        browser.close()
    trade.refresh_from_db()
    assert trade.unit_cost_fen == 82500
