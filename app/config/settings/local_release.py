import os

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403

if len(SECRET_KEY) < 50 or SECRET_KEY.startswith("django-insecure-"):  # noqa: F405
    raise ImproperlyConfigured("Local release requires a generated DJANGO_SECRET_KEY")
if (
    not os.environ.get("POSTGRES_PASSWORD")
    or os.environ["POSTGRES_PASSWORD"] == "seller-local-only"
):
    raise ImproperlyConfigured("Local release requires a private POSTGRES_PASSWORD")
if not os.environ.get("DJANGO_ALLOWED_HOSTS"):
    raise ImproperlyConfigured("Local release requires DJANGO_ALLOWED_HOSTS")

DEBUG = False
CSRF_TRUSTED_ORIGINS = [
    origin for origin in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if origin
]
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_SSL_REDIRECT = False
SECURE_HSTS_SECONDS = 0
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
LOCAL_BACKUP_ROOT = os.environ.get("LOCAL_BACKUP_ROOT", "/srv/backups")
RELEASE_VERSION = os.environ.get("RELEASE_VERSION", "development")
