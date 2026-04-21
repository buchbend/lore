"""Plan 3 adapters register into the shared registry on module import."""

from __future__ import annotations


def test_cursor_adapter_is_registered() -> None:
    from lore_adapters import get_adapter, registered_hosts

    assert "cursor" in registered_hosts()
    a = get_adapter("cursor")
    assert a.host == "cursor"


def test_copilot_adapter_is_registered() -> None:
    from lore_adapters import get_adapter, registered_hosts

    assert "copilot" in registered_hosts()
    a = get_adapter("copilot")
    assert a.host == "copilot"


def test_all_expected_hosts_present() -> None:
    from lore_adapters import registered_hosts

    hosts = set(registered_hosts())
    # Day-1: claude-code + manual-send.
    # Plan 3: cursor + copilot.
    assert {"claude-code", "manual-send", "cursor", "copilot"}.issubset(hosts)
