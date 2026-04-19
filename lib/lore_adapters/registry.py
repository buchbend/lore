"""Adapter registry: map host strings to Adapter instances."""

from __future__ import annotations

from lore_adapters.protocol import Adapter


class UnknownHostError(KeyError):
    """Raised when no adapter is registered for the requested host."""


def get_adapter(host: str) -> Adapter:
    """Return a registered adapter instance or raise UnknownHostError.

    Day-1 hosts: "claude-code", "manual-send".
    Future third-party adapters will plug in via entry-points (deferred);
    until then the registry is a private dict.
    """
    if host in _REGISTRY:
        return _REGISTRY[host]
    raise UnknownHostError(host)


def registered_hosts() -> list[str]:
    """List currently registered host names."""
    return sorted(_REGISTRY.keys())


def register(adapter: Adapter) -> None:
    """Register a new adapter instance. Overwrites any prior entry."""
    _REGISTRY[adapter.host] = adapter


# Registry populated at import time with the v1 adapters
from lore_adapters.claude_code import ClaudeCodeAdapter
from lore_adapters.manual_send import ManualSendAdapter


_REGISTRY: dict[str, Adapter] = {}
register(ClaudeCodeAdapter())
register(ManualSendAdapter())
