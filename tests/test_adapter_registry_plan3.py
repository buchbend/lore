"""Plan 3 adapters register into the shared registry on module import."""

from __future__ import annotations


def test_cursor_adapter_is_registered() -> None:
    from lore_adapters import get_adapter, registered_integrations

    assert "cursor" in registered_integrations()
    a = get_adapter("cursor")
    assert a.integration == "cursor"


def test_copilot_adapter_is_registered() -> None:
    from lore_adapters import get_adapter, registered_integrations

    assert "copilot" in registered_integrations()
    a = get_adapter("copilot")
    assert a.integration == "copilot"


def test_all_expected_hosts_present() -> None:
    from lore_adapters import registered_integrations

    hosts = set(registered_integrations())
    # Day-1: claude-code + manual-send.
    # Plan 3: cursor + copilot.
    assert {"claude-code", "manual-send", "cursor", "copilot"}.issubset(hosts)
