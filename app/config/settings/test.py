from .base import *  # noqa: F403

SECRET_KEY = "django-insecure-isolated-tests-only"
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
DATABASES["default"]["CONN_MAX_AGE"] = 0  # noqa: F405
CELERY_TASK_ALWAYS_EAGER = True
XGJ_APP_KEY = ""
XGJ_APP_SECRET = ""
