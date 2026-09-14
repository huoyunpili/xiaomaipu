from datetime import timedelta

import pytest
from django.utils import timezone

from app.accounts.models import LoginAttempt

pytestmark = pytest.mark.django_db


def test_login_limit_and_recovery(client, admin_user):
    for _ in range(10):
        assert client.post("/login/", {"username": "owner", "password": "wrong"}).status_code == 200
    assert (
        client.post("/login/", {"username": "owner", "password": "test-only-password"}).status_code
        == 429
    )
    assert all("owner" not in row.key for row in LoginAttempt.objects.all())
    LoginAttempt.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert (
        client.post("/login/", {"username": "owner", "password": "test-only-password"}).status_code
        == 302
    )


def test_success_resets_account_attempts(client, admin_user):
    for _ in range(9):
        client.post("/login/", {"username": "owner", "password": "wrong"})
    assert (
        client.post("/login/", {"username": "owner", "password": "test-only-password"}).status_code
        == 302
    )
    assert LoginAttempt.objects.count() == 1
