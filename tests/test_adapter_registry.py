"""Tests for the adapter registry."""

from __future__ import annotations

import pytest

from lore_adapters import (
    Adapter,
    UnknownIntegrationError,
    get_adapter,
    register,
    registered_integrations,
)
from lore_adapters.protocol import Adapter as AdapterProtocol


class StubAdapter(AdapterProtocol):
    """Minimal stub adapter for testing."""

    def __init__(self, integration: str) -> None:
        self._integration = integration

    @property
    def integration(self) -> str:
        """Integration name of this adapter."""
        return self._integration

    async def send(self, note_id: str, content: str) -> str:
        """Send a note somewhere."""
        return f"sent {note_id}"

    async def receive(self) -> str:
        """Receive a note."""
        return "received"

    async def list_inbox(self) -> list[str]:
        """List available note IDs in the inbox."""
        return []

    async def delete_note(self, note_id: str) -> None:
        """Delete a note from the inbox."""
        pass


def test_registry_returns_claude_code() -> None:
    """get_adapter("claude-code") returns the claude-code adapter."""
    adapter = get_adapter("claude-code")
    assert adapter.integration == "claude-code"


def test_registry_returns_manual_send() -> None:
    """get_adapter("manual-send") returns the manual-send adapter."""
    adapter = get_adapter("manual-send")
    assert adapter.integration == "manual-send"


def test_registry_unknown_integration_raises() -> None:
    """get_adapter with unknown integration raises UnknownIntegrationError."""
    with pytest.raises(UnknownIntegrationError):
        get_adapter("unknown")


def test_registered_integrations_lists_v1_set() -> None:
    """registered_integrations() returns a sorted list including v1 adapters."""
    integrations = registered_integrations()
    assert isinstance(integrations, list)
    assert "claude-code" in integrations
    assert "manual-send" in integrations
    # Should be sorted
    assert integrations == sorted(integrations)


def test_register_adds_new_adapter() -> None:
    """register() adds a new adapter and get_adapter can retrieve it."""
    stub = StubAdapter(integration="x")
    register(stub)

    # Should be retrievable
    adapter = get_adapter("x")
    assert adapter.integration == "x"

    # Should appear in registered_integrations
    integrations = registered_integrations()
    assert "x" in integrations


def test_init_exports_public_api() -> None:
    """from lore_adapters import ... works for the public API."""
    # This test just verifies the imports at the top of this file work.
    # If the imports fail, the test fails.
    assert callable(get_adapter)
    assert callable(register)
    assert callable(registered_integrations)
    assert UnknownIntegrationError is not None
    assert Adapter is not None
