import pytest
from django.db import connection

from app.common.backup import database_fingerprint
from app.shops.models import Shop


@pytest.mark.django_db
def test_fingerprint_detects_changed_content_with_equal_counts():
    shop = Shop.objects.create(name="Synthetic original")
    with connection.cursor() as cursor:
        before = database_fingerprint(cursor)
    Shop.objects.filter(pk=shop.pk).update(name="Synthetic changed")
    with connection.cursor() as cursor:
        after = database_fingerprint(cursor)
    assert before["shops_shop"]["rows"] == after["shops_shop"]["rows"]
    assert before["shops_shop"]["sha256"] != after["shops_shop"]["sha256"]
    assert "Synthetic" not in str(after)
    Shop.objects.filter(pk=shop.pk).update(name="Synthetic original")
    with connection.cursor() as cursor:
        assert database_fingerprint(cursor) == before
