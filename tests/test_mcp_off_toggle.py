"""MCP `_dispatch` refuses every tool when `/lore:off all` is active.

The MCP server inherits ``CLAUDE_SESSION_ID`` from its Claude Code
parent. When that sid has the `lore-off-<sid>` sentinel set, every
tool call returns an `_mcp_error("session_off", ...)` envelope without
touching the vault — the security-first contract from
``docs/architecture/slash-toggles.md``.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from lore_core import toggles


@pytest.fixture(autouse=True)
def _isolate_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    yield


def test_dispatch_refuses_when_off(monkeypatch: pytest.MonkeyPatch) -> None:
    sid = "mcp-sid-1"
    monkeypatch.setenv("CLAUDE_SESSION_ID", sid)
    toggles.set_off("all", sid)

    from lore_mcp.server import _dispatch

    # If short-circuit fails, handle_search would be called and try to read state.
    with patch("lore_mcp.server.handle_search") as mock_search:
        result = _dispatch("lore_search", {"query": "x"})

    mock_search.assert_not_called()
    assert "error" in result
    assert result["error"]["code"] == "session_off"


def test_dispatch_runs_when_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sanity: with no sentinel, the dispatcher routes normally."""
    sid = "mcp-sid-2"
    monkeypatch.setenv("CLAUDE_SESSION_ID", sid)

    from lore_mcp.server import _dispatch

    with patch("lore_mcp.server.handle_search", return_value={"hits": []}) as mock:
        result = _dispatch("lore_search", {"query": "x"})

    mock.assert_called_once()
    assert result == {"hits": []}


def test_dispatch_runs_when_no_sid(monkeypatch: pytest.MonkeyPatch) -> None:
    """No CLAUDE_SESSION_ID in env → can't scope a sentinel, hooks run normally."""
    # Sentinel for *some* sid set, but env doesn't name it.
    toggles.set_off("all", "some-other-sid")

    from lore_mcp.server import _dispatch

    with patch("lore_mcp.server.handle_search", return_value={"hits": []}) as mock:
        result = _dispatch("lore_search", {"query": "x"})

    mock.assert_called_once()
    assert result == {"hits": []}


def test_dispatch_refusal_envelope_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refusal envelope follows the standard `_mcp_error` shape with a `next` hint."""
    sid = "mcp-sid-3"
    monkeypatch.setenv("CLAUDE_SESSION_ID", sid)
    toggles.set_off("all", sid)

    from lore_mcp.server import _dispatch

    result = _dispatch("lore_read", {"path": "wiki/foo.md"})

    assert "error" in result
    err = result["error"]
    assert err["code"] == "session_off"
    assert "message" in err
    assert "next" in err  # tells the user how to re-enable
