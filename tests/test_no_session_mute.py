"""Lore has no session mute (#633): no `lore off`/`lore on`, no mute file read."""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch

import pytest
from lore_cli.__main__ import app
from typer.testing import CliRunner

SID = "sid-stale"


@pytest.fixture(autouse=True)
def _stale_mute_file(tmp_path, monkeypatch):
    """A mute file an older Lore left behind, for scope `all` and `citations`."""
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setenv("CLAUDE_SESSION_ID", SID)
    monkeypatch.delenv("LORE_CHILD_MODE", raising=False)
    for scope in ("all", "citations"):
        (tmp_path / f"lore-off-{scope}-{SID}").write_text("")


@pytest.mark.parametrize("verb", ["off", "on"])
def test_the_cli_offers_no_mute_verb(verb: str) -> None:
    result = CliRunner().invoke(app, [verb])
    assert result.exit_code != 0
    assert "No such command" in result.output


def test_a_hook_ignores_a_stale_mute_file(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = io.StringIO(json.dumps({"session_id": SID, "hook_event_name": "SessionStart"}))
    stream.isatty = lambda: False  # type: ignore[method-assign]
    monkeypatch.setattr("sys.stdin", stream)
    start = MagicMock(return_value="banner")
    with patch("lore_cli.hooks._session_start", start), patch("lore_cli.hooks._emit"):
        from lore_cli.hooks import cmd_session_start

        cmd_session_start(cwd="/tmp", plain=True, probe=True)
    start.assert_called()


def test_the_mcp_server_ignores_a_stale_mute_file() -> None:
    from lore_mcp.server import _dispatch

    with patch("lore_mcp.server.handle_search", return_value={"ok": 1}) as search:
        assert _dispatch("lore_search", {"query": "x"}) == {"ok": 1}
    search.assert_called_once()
