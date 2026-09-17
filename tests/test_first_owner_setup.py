import pytest
from django.test import Client

from app.accounts.models import User
from app.audit.models import AuditEvent

pytestmark = pytest.mark.django_db


def owner_payload(username="my-owner"):
    return {
        "username": username,
        "display_name": "我的店铺",
        "password1": "StoreOwner-Strong-2026!",
        "password2": "StoreOwner-Strong-2026!",
    }


def test_first_visit_creates_the_users_own_owner_account(client):
    response = client.get("/login/")
    assert response.status_code == 302
    assert response.url == "/setup/"

    response = client.get("/setup/")
    assert response.status_code == 200
    assert "创建你的店主账号" in response.content.decode()

    response = client.post("/setup/", owner_payload("自己的用户名"))
    assert response.status_code == 302
    assert response.url == "/"
    owner = User.objects.get()
    assert owner.username == "自己的用户名"
    assert owner.display_name == "我的店铺"
    assert owner.role == User.Role.ADMIN
    assert owner.is_superuser
    assert owner.check_password("StoreOwner-Strong-2026!")
    assert client.get("/").status_code == 200


def test_setup_closes_after_an_administrator_exists(client, admin_user):
    assert client.get("/setup/").url == "/login/"
    assert client.get("/login/").status_code == 200
    response = client.post("/setup/", owner_payload("second-owner"))
    assert response.status_code == 302
    assert response.url == "/login/"
    assert User.objects.count() == 1


def test_unclaimed_development_owner_can_be_renamed_without_changing_identity(client, settings):
    settings.DEBUG = True
    original = User.objects.create_superuser(
        "dev-owner", password="temporary-password-before-claim"
    )
    response = client.post("/setup/", owner_payload("remembered-owner"))
    assert response.status_code == 302
    original.refresh_from_db()
    assert original.username == "remembered-owner"
    assert original.check_password("StoreOwner-Strong-2026!")
    assert User.objects.count() == 1


def test_first_owner_setup_requires_csrf():
    client = Client(enforce_csrf_checks=True)
    assert client.post("/setup/", owner_payload()).status_code == 403


def test_invalid_password_does_not_create_an_owner(client):
    payload = owner_payload()
    payload["password1"] = payload["password2"] = "123"
    response = client.post("/setup/", payload)
    assert response.status_code == 200
    assert User.objects.count() == 0
    assert response.context["form"].errors


def test_signed_in_user_can_choose_a_memorable_username_and_password(client, admin_user):
    client.force_login(admin_user)
    response = client.post(
        "/account/",
        {
            "username": "我的常用账号",
            "current_password": "test-only-password",
            "display_name": "店主本人",
            "password1": "My-Memorable-Password-2026!",
            "password2": "My-Memorable-Password-2026!",
        },
    )
    assert response.status_code == 302
    assert response.url == "/account/"
    admin_user.refresh_from_db()
    assert admin_user.username == "我的常用账号"
    assert admin_user.display_name == "店主本人"
    assert admin_user.check_password("My-Memorable-Password-2026!")
    assert client.get("/account/").status_code == 200
    event = AuditEvent.objects.get(action="account.updated")
    assert event.details == {"username_changed": True, "password_changed": True}


def test_account_page_requires_login(client):
    response = client.get("/account/")
    assert response.status_code == 302
    assert response.url.startswith("/login/")


def test_mismatched_new_password_keeps_existing_credentials(client, admin_user):
    client.force_login(admin_user)
    response = client.post(
        "/account/",
        {
            "username": admin_user.username,
            "display_name": "",
            "password1": "First-New-Password-2026!",
            "password2": "Different-New-Password-2026!",
        },
    )
    assert response.status_code == 200
    admin_user.refresh_from_db()
    assert admin_user.check_password("test-only-password")
    assert not AuditEvent.objects.filter(action="account.updated").exists()
