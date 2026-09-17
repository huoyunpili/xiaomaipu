from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from app.accounts.models import User
from tests.test_first_owner_setup import owner_payload

pytestmark = pytest.mark.django_db


def test_disabled_owner_does_not_reopen_setup(client, admin_user):
    admin_user.is_active = False
    admin_user.save()
    assert client.get("/login/").status_code == 200
    assert client.post("/setup/", owner_payload("intruder")).url == "/login/"
    assert not User.objects.filter(username="intruder").exists()


def test_release_does_not_allow_claiming_development_owner(client, settings):
    settings.DEBUG = False
    owner = User.objects.create_superuser("dev-owner", password="existing-password")
    assert client.post("/setup/", owner_payload("intruder")).url == "/login/"
    owner.refresh_from_db()
    assert owner.username == "dev-owner"


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/workspace/shipping/",
        "/workspace/orders/",
        "/workspace/profits/",
        "/workspace/settings/",
        "/account/",
        "/audit/",
    ],
)
def test_private_pages_require_login(client, admin_user, path):
    response = client.get(path)
    assert response.status_code == 302
    assert response.url.startswith("/login/?next=")


@pytest.mark.parametrize(
    "target", ["https://example.org/", "//example.org/", "/workspace/shipping/"]
)
def test_login_redirects_only_to_local_pages(client, admin_user, target):
    response = client.post(
        "/login/", {"username": "owner", "password": "test-only-password", "next": target}
    )
    assert response.url == (target if target.startswith("/workspace/") else "/")


def test_logged_in_login_page_redirects(client, admin_user):
    client.force_login(admin_user)
    assert client.get("/login/").url == "/"


def test_equivalent_username_spelling_shares_login_limit(client, admin_user):
    for _ in range(10):
        assert (
            client.post("/login/", {"username": "ｏｗｎｅｒ", "password": "wrong"}).status_code
            == 200
        )
    assert client.post("/login/", {"username": "owner", "password": "wrong"}).status_code == 429


@pytest.mark.parametrize("current", ["", "wrong"])
def test_sensitive_account_changes_require_current_password(client, admin_user, current):
    client.force_login(admin_user)
    response = client.post(
        "/account/",
        {
            "username": "changed-owner",
            "display_name": "",
            "current_password": current,
            "password1": "Changed-Strong-2026!",
            "password2": "Changed-Strong-2026!",
        },
    )
    assert response.status_code == 200
    admin_user.refresh_from_db()
    assert admin_user.username == "owner"
    assert admin_user.check_password("test-only-password")


def test_password_change_keeps_current_session_and_invalidates_other_sessions(client, admin_user):
    other = Client()
    client.force_login(admin_user)
    other.force_login(admin_user)
    response = client.post(
        "/account/",
        {
            "username": "owner",
            "display_name": "",
            "current_password": "test-only-password",
            "password1": "Changed-Strong-2026!",
            "password2": "Changed-Strong-2026!",
        },
    )
    assert response.status_code == 302
    assert client.get("/account/").status_code == 200
    assert other.get("/account/").url.startswith("/login/")


def test_expired_session_returns_to_login(client, admin_user):
    client.force_login(admin_user)
    session = client.session
    session.set_expiry(timezone.now() - timedelta(seconds=1))
    session.save()
    assert client.get("/account/").url.startswith("/login/")


def test_login_and_logout_require_csrf(admin_user):
    client = Client(enforce_csrf_checks=True)
    assert (
        client.post("/login/", {"username": "owner", "password": "test-only-password"}).status_code
        == 403
    )
    client.force_login(admin_user)
    assert client.post("/logout/").status_code == 403
    client.get("/account/")
    token = client.cookies["csrftoken"].value
    assert client.post("/logout/", {"csrfmiddlewaretoken": token}).status_code == 302
    assert client.get("/account/").url.startswith("/login/")
