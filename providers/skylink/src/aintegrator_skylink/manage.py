"""Django management entry point: ``python -m aintegrator_skylink.manage``."""

from __future__ import annotations

import os
import sys

from django.core.management import execute_from_command_line


def main() -> None:
    """Configure the settings module and dispatch the management command."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "aintegrator_skylink.settings")
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
