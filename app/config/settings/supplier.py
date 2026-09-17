import secrets

from .base import *  # noqa: F403

DEBUG = False
ROOT_URLCONF = "app.config.supplier_urls"
ALLOWED_HOSTS = ["127.0.0.1", "localhost", ".trycloudflare.com"]
SECRET_KEY = SECRET_KEY or secrets.token_urlsafe(48)  # noqa: F405
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_NAME = "supplier_session"
CSRF_COOKIE_NAME = "supplier_csrf"
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_REFERRER_POLICY = "no-referrer"
FILE_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
