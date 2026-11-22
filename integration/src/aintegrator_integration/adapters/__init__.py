"""One adapter per simulated provider. Adapters talk HTTP/SOAP only."""

from aintegrator_integration.adapters.fibernet import FibernetAdapter
from aintegrator_integration.adapters.legacytel import LegacytelAdapter
from aintegrator_integration.adapters.skylink import SkylinkAdapter

__all__ = ["FibernetAdapter", "LegacytelAdapter", "SkylinkAdapter"]
