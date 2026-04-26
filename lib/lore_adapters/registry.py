"""Adapter registry: map integration strings to Adapter instances."""

from __future__ import annotations

from lore_adapters.protocol import Adapter

# Module-level registry — declared FIRST so `register()` calls during
# module-import (including any from third-party adapter modules that
# self-register at import time) write into the canonical dict.
_REGISTRY: dict[str, Adapter] = {}


class UnknownIntegrationError(KeyError):
    """Raised when no adapter is registered for the requested integration."""


def get_adapter(integration: str) -> Adapter:
    """Return a registered adapter instance or raise UnknownIntegrationError.

    Day-1 integrations: "claude-code", "manual-send".
    Future third-party adapters will plug in via entry-points (deferred);
    until then the registry is a private dict.
    """
    if integration in _REGISTRY:
        return _REGISTRY[integration]
    raise UnknownIntegrationError(integration)


def registered_integrations() -> list[str]:
    """List currently registered integration names."""
    return sorted(_REGISTRY.keys())


def register(adapter: Adapter) -> None:
    """Register a new adapter instance. Overwrites any prior entry."""
    _REGISTRY[adapter.integration] = adapter


# Built-in adapters — imported and registered after `_REGISTRY` is alive.
from lore_adapters.claude_code import ClaudeCodeAdapter  # noqa: E402
from lore_adapters.cursor_agent import CursorAgentAdapter  # noqa: E402
from lore_adapters.manual_send import ManualSendAdapter  # noqa: E402
from lore_adapters.vscode_copilot import VSCodeCopilotAdapter  # noqa: E402

register(ClaudeCodeAdapter())
register(CursorAgentAdapter())
register(ManualSendAdapter())
register(VSCodeCopilotAdapter())
