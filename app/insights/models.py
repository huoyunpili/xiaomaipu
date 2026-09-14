from django.db import models

from app.common.models import BaseModel


class MarketPrice(BaseModel):
    sku = models.ForeignKey("catalog.SKU", on_delete=models.PROTECT, related_name="market_prices")
    price_fen = models.PositiveBigIntegerField()
    observed_on = models.DateField()
    source = models.CharField(max_length=300)

    class Meta:
        ordering = ["-observed_on", "-created_at"]


class MarketEvent(BaseModel):
    sku = models.ForeignKey("catalog.SKU", on_delete=models.PROTECT, related_name="market_events")
    title = models.CharField(max_length=200)
    event_date = models.DateField()
    source = models.URLField(max_length=500, blank=True)
    notes = models.TextField(blank=True)
    confirmed = models.BooleanField(default=False)

    class Meta:
        ordering = ["event_date", "id"]


class IntelSource(BaseModel):
    name = models.CharField(max_length=100)
    url = models.URLField(max_length=500, unique=True)
    enabled = models.BooleanField(default=False)
    last_success = models.DateTimeField(null=True, blank=True)
    error = models.CharField(max_length=300, blank=True)


class IntelArticle(BaseModel):
    source = models.ForeignKey(IntelSource, on_delete=models.PROTECT)
    url = models.URLField(max_length=1000, unique=True)
    title = models.CharField(max_length=300)
    published = models.CharField(max_length=100, blank=True)
    matched_sku = models.ForeignKey("catalog.SKU", null=True, blank=True, on_delete=models.PROTECT)

    class Meta:
        ordering = ["-created_at"]
