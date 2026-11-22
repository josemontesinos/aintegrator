"""Unified integration layer: domain model, gateways, service and persistence."""

from aintegrator_integration.config import Settings, get_settings
from aintegrator_integration.factory import build_mcp_service, build_service, get_service
from aintegrator_integration.security import (
    ApprovalService,
    Redactor,
    ToolProfile,
    ToolSecurity,
    build_tool_security,
)
from aintegrator_integration.service import ConnectivityService

__all__ = [
    "ApprovalService",
    "ConnectivityService",
    "Redactor",
    "Settings",
    "ToolProfile",
    "ToolSecurity",
    "__version__",
    "build_mcp_service",
    "build_service",
    "build_tool_security",
    "get_service",
    "get_settings",
]

__version__ = "0.1.0"
