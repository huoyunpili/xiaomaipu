"""Settings for the self-contained Windows installer.

The installer carries Python and PostgreSQL.  Celery uses a local filesystem
broker, so an installed copy does not need Docker or Redis.
"""

import os
from pathlib import Path
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403

data_root_value = os.environ.get("FISH_MANAGER_DATA", "").strip()
if not data_root_value:
    raise ImproperlyConfigured("FISH_MANAGER_DATA must be set")
data_root = Path(data_root_value).resolve()
if not data_root.is_absolute():
    raise ImproperlyConfigured("FISH_MANAGER_DATA must be an absolute path")

if len(SECRET_KEY) < 50 or SECRET_KEY.startswith("django-insecure-"):  # noqa: F405
    raise ImproperlyConfigured("The Windows release requires a generated DJANGO_SECRET_KEY")

DEBUG = False
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
local_port = urlsplit(os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8765")).port or 80
CSRF_TRUSTED_ORIGINS = [f"http://127.0.0.1:{local_port}", f"http://localhost:{local_port}"]
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_SSL_REDIRECT = False
SECURE_HSTS_SECONDS = 0
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

DATABASES["default"].update(  # noqa: F405
    {
        "HOST": "127.0.0.1",
        "PORT": os.environ.get("POSTGRES_PORT", "55433"),
        "CONN_MAX_AGE": 60,
    }
)

PRIVATE_MEDIA_ROOT = data_root / "private-media"
LOCAL_BACKUP_ROOT = data_root / "backups"
XGJ_CREDENTIALS_FILE = data_root / "xgj-api.json"
RELEASE_VERSION = os.environ.get("RELEASE_VERSION", "development")

# The filesystem transport is sufficient for this single-computer product and
# removes the entire Redis installation/service from the user-facing release.
broker_root = data_root / "queue"
CELERY_BROKER_URL = "filesystem://"
CELERY_BROKER_TRANSPORT_OPTIONS = {
    "control_folder": str(broker_root / "control"),
    "data_folder_in": str(broker_root / "messages"),
    "data_folder_out": str(broker_root / "messages"),
    "data_folder_processed": str(broker_root / "processed"),
}
CELERY_WORKER_PREFETCH_MULTIPLIER = 1

MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")  # noqa: F405
FISH_DESKTOP_TOKEN = os.environ.get("FISH_DESKTOP_TOKEN", "")
if FISH_DESKTOP_TOKEN:
    MIDDLEWARE.insert(0, "app.desktop.middleware.DesktopSessionMiddleware")  # noqa: F405
STATIC_ROOT = BASE_DIR / "staticfiles"  # noqa: F405
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
