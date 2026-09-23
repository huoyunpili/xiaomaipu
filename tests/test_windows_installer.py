import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_windows_release_uses_bundled_postgres_without_redis_or_docker(tmp_path):
    environment = os.environ.copy()
    environment.update(
        {
            "DJANGO_SETTINGS_MODULE": "app.config.settings.windows_release",
            "DJANGO_SECRET_KEY": "a" * 64,
            "POSTGRES_PASSWORD": "private-test-password",
            "FISH_MANAGER_DATA": str(tmp_path),
        }
    )
    code = (
        "import django; django.setup(); "
        "from django.conf import settings; "
        "assert settings.DATABASES['default']['ENGINE'] == 'django.db.backends.postgresql'; "
        "assert settings.CELERY_BROKER_URL == 'filesystem://'; "
        "assert settings.PRIVATE_MEDIA_ROOT.name == 'private-media'; "
        "assert 'whitenoise.middleware.WhiteNoiseMiddleware' in settings.MIDDLEWARE"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr


def test_windows_installer_is_the_single_complete_user_path():
    runtime_script = (ROOT / "scripts/windows_release.ps1").read_text(encoding="utf-8")
    build_script = (ROOT / "scripts/build_windows_installer.ps1").read_text(encoding="utf-8")
    installer = (ROOT / "installer/fish-manager.iss").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Docker" not in runtime_script
    assert "redis" not in runtime_script.lower()
    assert "postgresql/bin" in runtime_script
    assert "tunnel=@(" not in runtime_script
    assert "'cloudflared.exe'),@('tunnel'" not in runtime_script
    assert "trycloudflare.com" not in runtime_script
    assert "SUPPLIER_PUBLIC_URL" not in runtime_script
    assert "serve_supplier" not in runtime_script
    assert "supplier=@(" not in runtime_script
    assert "README.md') -Destination $stageRoot" not in build_script
    assert "Join-Path $projectRoot 'docs'" not in build_script
    assert "Join-Path $projectRoot 'LICENSE'" in build_script
    assert "Join-Path $projectRoot 'NOTICE'" in build_script
    assert "cloudflared" not in build_script.lower()
    assert "../site-packages/win32/lib" in build_script
    assert "pywin32>=306" in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "Remove-ObsoleteTunnel" in runtime_script
    assert (
        "Refusing to remove an obsolete tunnel outside the application directory" in runtime_script
    )
    assert "'Install' {\n        Stop-All\n        Remove-ObsoleteTunnel" in runtime_script
    assert "LicenseFile={#SourceRoot}\\LICENSE" in installer
    assert "AppVersion=0.6.0" in installer
    assert "OutputBaseFilename=鱼管家-0.6.0-安装程序" in installer
    assert "-Action Install" in installer
    assert "{userstartup}" in installer
    assert "普通用户只保留一种方式" in readme
