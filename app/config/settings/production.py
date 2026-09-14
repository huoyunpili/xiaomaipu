import os

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403

if len(SECRET_KEY) < 50 or SECRET_KEY.startswith("django-insecure-"):  # noqa: F405
    raise ImproperlyConfigured("Production requires DJANGO_SECRET_KEY of at least 50 characters")
if (
    not os.environ.get("POSTGRES_PASSWORD")
    or os.environ["POSTGRES_PASSWORD"] == "seller-local-only"
):  # noqa: F405
    raise ImproperlyConfigured("Production requires POSTGRES_PASSWORD")
if not os.environ.get("DJANGO_ALLOWED_HOSTS"):  # noqa: F405
    raise ImproperlyConfigured("Production requires DJANGO_ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",")  # noqa: F405
CSRF_TRUSTED_ORIGINS = [origin for origin in CSRF_TRUSTED_ORIGINS if origin]
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
# Only Caddy may reach the production web container.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
