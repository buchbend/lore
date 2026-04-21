"""Adapter registry: map host strings to Adapter instances."""

from __future__ import annotations

from lore_adapters.protocol import Adapter


# Module-level registry — declared FIRST so `register()` calls during
# module-import (including any from third-party adapter modules that
# self-register at import time) write into the canonical dict.
_REGISTRY: dict[str, Adapter] = {}


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


# Built-in adapters — imported and registered after `_REGISTRY` is alive.
from lore_adapters.claude_code import ClaudeCodeAdapter  # noqa: E402
from lore_adapters.cursor_agent import CursorAgentAdapter  # noqa: E402
from lore_adapters.manual_send import ManualSendAdapter  # noqa: E402
from lore_adapters.vscode_copilot import VSCodeCopilotAdapter  # noqa: E402

register(ClaudeCodeAdapter())
register(CursorAgentAdapter())
register(ManualSendAdapter())
register(VSCodeCopilotAdapter())
