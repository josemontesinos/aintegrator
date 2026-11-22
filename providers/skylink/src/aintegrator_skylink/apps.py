"""App configuration and background worker startup."""

from __future__ import annotations

import sys

from django.apps import AppConfig
from django.conf import settings

# Management commands that must not start an advancing worker in their process.
_PASSIVE_COMMANDS = frozenset(
    {
        "check",
        "collectstatic",
        "createsuperuser",
        "deliver_webhooks",
        "makemigrations",
        "migrate",
        "shell",
        "test",
    }
)


class SkylinkConfig(AppConfig):
    """Register the provider and start its in-process advance worker."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "aintegrator_skylink"
    verbose_name = "SkyLink simulated provider"

    def ready(self) -> None:
        """Start the worker thread unless disabled or running a passive command."""
        command = sys.argv[1] if len(sys.argv) > 1 else ""
        if not settings.SKYLINK_WORKER_ENABLED or command in _PASSIVE_COMMANDS:
            return
        from .worker import start_worker

        start_worker()
