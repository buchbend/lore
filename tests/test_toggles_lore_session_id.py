"""`lore off` / `lore on` and the hook mute check work in an OpenCode session.

The OpenCode plugin sets ``LORE_SESSION_ID`` (not ``CLAUDE_SESSION_ID``)
for hooks and the agent shell. ``LORE_SESSION_ID`` wins over an inherited
``CLAUDE_SESSION_ID``, as in ``lore_cli.context_cache``.
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pytest
from lore_cli.__main__ import app
from lore_core import toggles
from typer.testing import CliRunner


@pytest.fixture(autouse=True)
def _isolate_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.delenv("LORE_CHILD_MODE", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    monkeypatch.setenv("LORE_SESSION_ID", "ses_oc1")
    stream = io.StringIO("")
    stream.isatty = lambda: True  # type: ignore[method-assign]
    monkeypatch.setattr("sys.stdin", stream)


def test_session_id_prefers_claude_session_id(monkeypatch):
    # A Claude Code run inside an OpenCode shell inherits LORE_SESSION_ID.
    monkeypatch.setenv("CLAUDE_SESSION_ID", "outer-claude")
    assert toggles.session_id() == "outer-claude"


def test_context_cache_session_id_prefers_claude_session_id(monkeypatch):
    from lore_cli import context_cache

    monkeypatch.setenv("CLAUDE_SESSION_ID", "outer-claude")
    assert context_cache._session_id() == "sid-outer-claude"


def test_session_id_falls_back_to_claude(monkeypatch):
    monkeypatch.delenv("LORE_SESSION_ID")
    monkeypatch.setenv("CLAUDE_SESSION_ID", "cc-1")
    assert toggles.session_id() == "cc-1"


def test_lore_off_uses_lore_session_id():
    result = CliRunner().invoke(app, ["off"])
    assert result.exit_code == 0, result.output
    assert toggles.is_off("all", "ses_oc1")


def test_session_start_noop_when_off_in_opencode():
    toggles.set_off("all", "ses_oc1")
    mock_session = MagicMock()
    with patch("lore_cli.hooks._session_start", mock_session):
        from lore_cli.hooks import cmd_session_start

        cmd_session_start(cwd="/tmp", plain=True, probe=False)
    mock_session.assert_not_called()


def test_session_id_rejects_a_path_unsafe_value(monkeypatch):
    monkeypatch.setenv("LORE_SESSION_ID", "../../etc/x")
    assert toggles.session_id() is None


def test_set_off_rejects_a_path_unsafe_sid():
    with pytest.raises(ValueError):
        toggles.set_off("all", "a/../b")
