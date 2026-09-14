import pytest
from django.core.management import call_command

from app.accounts.models import User


@pytest.fixture
def shop(db):
    from app.shops.models import Shop

    call_command("bootstrap")
    return Shop.objects.get(is_active=True)


@pytest.fixture
def admin_user(db):
    return User.objects.create_user("owner", password="test-only-password", role=User.Role.ADMIN)


@pytest.fixture
def operator(db):
    return User.objects.create_user("staff", password="test-only-password")
