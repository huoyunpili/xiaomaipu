from datetime import timedelta

import pytest
from django.utils import timezone

from app.insights.metrics import active_orders, order_totals, product_metrics
from app.insights.models import IntelArticle, IntelSource, MarketEvent, MarketPrice
from app.insights.tasks import ingest_feed
from tests.test_business import action, draft, goods, money

pytestmark = pytest.mark.django_db


def test_risk_price_expiry_confirmed_events_and_sales(admin_user, shop):
    sku, lot = goods(admin_user, quantity=3)
    order = draft(admin_user, sku)
    action(admin_user, order, "confirm")
    action(admin_user, order, "ship", delivery_method="HANDOVER")
    today = timezone.localdate()
    MarketPrice.objects.create(sku=sku, price_fen=9000, observed_on=today, source="人工核对报价")
    MarketEvent.objects.create(
        sku=sku, title="新品", event_date=today + timedelta(days=10), confirmed=True
    )
    MarketEvent.objects.create(
        sku=sku, title="传闻", event_date=today + timedelta(days=5), confirmed=False
    )
    metric = product_metrics(sku)
    assert metric["qty30"] == 1 and metric["stock_days"] == 60
    assert metric["loss_fen"] == 3000
    assert metric["events"].count() == 1
    MarketPrice.objects.update(observed_on=today - timedelta(days=31))
    assert product_metrics(sku)["loss_fen"] is None


def test_reports_include_cancelled_order_cash(admin_user, shop):
    sku, lot = goods(admin_user)
    order = draft(admin_user, sku)
    action(admin_user, order, "confirm")
    money(admin_user, order, "RECEIPT", 10000)
    action(admin_user, order, "cancel", reason="客户取消")
    totals = order_totals(active_orders())
    assert totals["received"] == 10000 and totals["profit"] == 0
    money(admin_user, order, "REFUND", 10000)
    assert order_totals(active_orders())["refunded"] == 10000


def test_rss_dedup_and_title_match_are_not_confirmed_risks(admin_user):
    sku, lot = goods(admin_user)
    source = IntelSource.objects.create(name="测试源", url="https://example.com/feed")
    raw = f"<rss><channel><item><title>{sku.product.name} 新品消息</title><link>https://example.com/item</link></item></channel></rss>".encode()
    ingest_feed(source, raw)
    ingest_feed(source, raw)
    assert IntelArticle.objects.count() == 1
    assert IntelArticle.objects.get().matched_sku_id == sku.pk
    assert not MarketEvent.objects.exists()


def test_new_pages_render(client, admin_user, shop):
    goods(admin_user)
    client.force_login(admin_user)
    for url in (
        "/",
        "/customers/",
        "/customers/new/",
        "/reports/",
        "/risks/",
        "/risks/sources/new/",
    ):
        assert client.get(url).status_code == 200, url
    assert client.get("/imports/").status_code == 302
    assert client.get("/imports/").url == "/integrations/xgj/"
