"""Unified integration layer: domain model, gateways, service and persistence."""

from aintegrator_integration.config import Settings, get_settings
from aintegrator_integration.factory import build_service, get_service
from aintegrator_integration.service import ConnectivityService

__all__ = [
    "ConnectivityService",
    "Settings",
    "__version__",
    "build_service",
    "get_service",
    "get_settings",
]

__version__ = "0.1.0"
