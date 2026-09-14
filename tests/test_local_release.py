import io
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.test import override_settings

from app.accounts.models import User
from app.common.release import local_release_status

pytestmark = pytest.mark.django_db


def test_release_health_endpoints(client):
    assert client.get("/health/live/").json() == {"status": "ok"}
    assert client.get("/health/ready/").json() == {"status": "ok"}


def test_release_status_reports_latest_verified_backup(tmp_path):
    older = tmp_path / "20260101-000000"
    latest = tmp_path / "20260102-000000"
    older.mkdir()
    latest.mkdir()
    (older / "manifest.json").write_text(
        json.dumps({"created_at": "old", "restored": False}), encoding="utf-8"
    )
    (latest / "manifest.json").write_text(
        json.dumps({"created_at": "new", "restored": True}), encoding="utf-8"
    )
    with override_settings(LOCAL_BACKUP_ROOT=tmp_path, RELEASE_VERSION="test-rc"):
        assert local_release_status() == {
            "version": "test-rc",
            "last_backup_at": "new",
            "last_backup_verified": True,
            "backup_error": "",
        }


def test_release_status_does_not_expose_invalid_manifest(tmp_path):
    target = tmp_path / "20260101-000000"
    target.mkdir()
    (target / "manifest.json").write_text("{invalid", encoding="utf-8")
    with override_settings(LOCAL_BACKUP_ROOT=tmp_path):
        result = local_release_status()
    assert result["last_backup_at"] is None
    assert result["backup_error"]
    assert "invalid" not in result["backup_error"]


def test_init_local_admin_preserves_existing_admin():
    User.objects.create_user("owner", role=User.Role.ADMIN)
    output = io.StringIO()
    with patch("app.accounts.management.commands.init_local_admin.call_command") as nested:
        call_command("init_local_admin", stdout=output)
    nested.assert_not_called()
    assert User.objects.count() == 1


def test_local_release_settings_require_private_secrets():
    environment = os.environ.copy()
    environment.update(
        {
            "DJANGO_SETTINGS_MODULE": "app.config.settings.local_release",
            "DJANGO_SECRET_KEY": "short",
            "POSTGRES_PASSWORD": "seller-local-only",
            "DJANGO_ALLOWED_HOSTS": "localhost",
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", "import django; django.setup()"],
        cwd=Path(__file__).parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode != 0
    assert "generated DJANGO_SECRET_KEY" in result.stderr


def test_local_compose_keeps_datastores_private():
    compose = (Path(__file__).parents[1] / "deployment" / "compose.local.yaml").read_text(
        encoding="utf-8"
    )
    postgres = compose.split("  postgres:", 1)[1].split("  redis:", 1)[0]
    redis = compose.split("  redis:", 1)[1].split("  web:", 1)[0]
    caddy = compose.split("  caddy:", 1)[1]
    assert "ports:" not in postgres
    assert "ports:" not in redis
    assert "ports:" in caddy
    assert "127.0.0.1" in caddy
    assert "app.config.settings.local_release" in compose
