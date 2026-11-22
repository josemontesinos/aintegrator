"""Simulated provider A: FastAPI REST/JSON with OAuth2 and idempotency."""

from ._version import __version__
from .app import app

__all__ = ["__version__", "app"]
