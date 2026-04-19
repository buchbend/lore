"""Host adapters — convert host-native transcripts to canonical lore format."""

from __future__ import annotations

from lore_adapters.protocol import Adapter
from lore_adapters.registry import (
    UnknownHostError,
    get_adapter,
    register,
    registered_hosts,
)

__all__ = [
    "Adapter",
    "UnknownHostError",
    "get_adapter",
    "register",
    "registered_hosts",
]
