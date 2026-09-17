import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[3]
load_dotenv(BASE_DIR / ".env.local", override=False)
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
DEBUG = False
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "app.accounts",
    "app.shops",
    "app.common",
    "app.audit",
    "app.catalog",
    "app.inventory",
    "app.orders",
    "app.finance",
    "app.procurement",
    "app.contacts",
    "app.evidence",
    "app.importing",
    "app.insights",
    "app.integrations",
    "app.operations",
    "app.workbench",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "app.common.middleware.RequestContextMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "app.config.urls"
WSGI_APPLICATION = "app.config.wsgi.application"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "app/templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "seller"),
        "USER": os.environ.get("POSTGRES_USER", "seller"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "seller-local-only"),
        "HOST": os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        "PORT": os.environ.get("POSTGRES_PORT", "55432"),
        "CONN_MAX_AGE": 60,
        "OPTIONS": {"connect_timeout": 5},
    }
}
AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
PRIVATE_MEDIA_ROOT = Path(
    os.environ.get("PRIVATE_MEDIA_ROOT", str(BASE_DIR / ".local/private-media"))
)
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "")
STATICFILES_DIRS = [BASE_DIR / "app/static"]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
CELERY_BROKER_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:56379/0")
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_IGNORE_RESULT = True
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULE = {
    "supplier-shipping": {"task": "app.workbench.tasks.poll_supplier_dispatches", "schedule": 30.0},
    "cleanup-export-images": {"task": "app.workbench.tasks.cleanup_exports", "schedule": 86400.0},
    "xgj-poll": {"task": "app.integrations.tasks.poll_orders", "schedule": 300.0},
    "daily-market-intel": {"task": "app.insights.tasks.collect_market_intel", "schedule": 86400.0},
}
XGJ_APP_KEY = os.environ.get("XGJ_APP_KEY", "")
XGJ_APP_SECRET = os.environ.get("XGJ_APP_SECRET", "")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": "app.common.logging.SafeJsonFormatter"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {"django.server": {"handlers": ["console"], "propagate": False}},
}
