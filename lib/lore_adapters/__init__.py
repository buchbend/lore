"""Integration adapters — convert integration-native transcripts to canonical lore format."""

from __future__ import annotations

from lore_adapters.protocol import Adapter
from lore_adapters.registry import (
    UnknownIntegrationError,
    get_adapter,
    register,
    registered_integrations,
)

__all__ = [
    "Adapter",
    "UnknownIntegrationError",
    "get_adapter",
    "register",
    "registered_integrations",
]
