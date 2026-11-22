"""Bootstrap Django for skylink unit tests (pytest-django is not a dependency)."""

from __future__ import annotations

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "aintegrator_skylink.settings")
os.environ["SKYLINK_WORKER_ENABLED"] = "0"
os.environ["SKYLINK_DB_PATH"] = ":memory:"

import django
from django.apps import apps

if not apps.ready:
    django.setup()

from django.core.management import call_command

call_command("migrate", verbosity=0, interactive=False)
