"""Django settings for the simulated ``skylink`` provider (provider C).

Every tunable is read from the environment so the service can be started from
``docker compose`` or from a test process without editing this module. Numeric
values are clamped to documented ranges to keep fault scenarios predictable.
"""

from __future__ import annotations

import os


def _number[N: (int, float)](name: str, default: N, minimum: N, maximum: N) -> N:
    """Read a numeric setting and clamp it to ``[minimum, maximum]``."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = type(default)(raw)
    except (TypeError, ValueError):
        return default
    return min(max(value, minimum), maximum)


SECRET_KEY = os.environ.get("SKYLINK_DJANGO_SECRET_KEY", "skylink-development-secret-key")
DEBUG = os.environ.get("SKYLINK_DEBUG", "0") == "1"
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "aintegrator_skylink.apps.SkylinkConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "aintegrator_skylink.urls"
APPEND_SLASH = False

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.request",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("SKYLINK_DB_PATH", "/tmp/aintegrator_skylink.sqlite3"),
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
STATIC_URL = "static/"

REST_FRAMEWORK: dict[str, object] = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [],
    "EXCEPTION_HANDLER": "aintegrator_skylink.api.api_exception_handler",
    "UNAUTHENTICATED_USER": None,
}

# Provider-specific configuration. All values are also available as django settings.
SKYLINK_API_KEY = os.environ.get("SKYLINK_API_KEY", "skylink-key")
SKYLINK_WEBHOOK_SECRET = os.environ.get("SKYLINK_WEBHOOK_SECRET", "skylink-secret")
SKYLINK_WEBHOOK_URL = os.environ.get(
    "SKYLINK_WEBHOOK_URL", "http://localhost:8000/webhooks/skylink"
)
SKYLINK_SEED = _number("SKYLINK_SEED", 20261122, 0, 2**31 - 1)
SKYLINK_AVAILABILITY_DELAY_S = _number("SKYLINK_AVAILABILITY_DELAY_S", 2, 2, 60)
SKYLINK_ORDER_STEP_DELAY_S = _number("SKYLINK_ORDER_STEP_DELAY_S", 1.0, 0.0, 60.0)
SKYLINK_WORKER_ENABLED = os.environ.get("SKYLINK_WORKER_ENABLED", "1") != "0"
SKYLINK_WORKER_POLL_INTERVAL_S = _number("SKYLINK_WORKER_POLL_INTERVAL_S", 0.5, 0.01, 60.0)
SKYLINK_DELIVERY_BATCH_SIZE = _number("SKYLINK_DELIVERY_BATCH_SIZE", 10, 1, 200)
SKYLINK_DELIVERY_LOOP_INTERVAL_S = _number("SKYLINK_DELIVERY_LOOP_INTERVAL_S", 0.2, 0.0, 60.0)
SKYLINK_DELIVERY_RETRY_BASE_S = _number("SKYLINK_DELIVERY_RETRY_BASE_S", 0.1, 0.0, 30.0)
SKYLINK_MAX_DELIVERY_ATTEMPTS = _number("SKYLINK_MAX_DELIVERY_ATTEMPTS", 5, 1, 20)
