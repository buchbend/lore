"""Hooks short-circuit when `/lore:off` (scope=all) is active for the session.

Mirrors the curator-mode guard pattern in
``test_curator_mode_guard.py``. Every hook entry point checks
``lore_core.toggles.is_off("all", sid)`` after reading the payload and
returns without emitting when the sentinel is present.

The sentinel resolves the session id via ``CLAUDE_SESSION_ID``, the
same env var the v0.13.1 ``_read_hook_payload`` republishes from the
Claude Code stdin payload.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lore_core import toggles


@pytest.fixture(autouse=True)
def _isolate_env(tmp_path, monkeypatch):
    """Per-test TMPDIR + clean curator-mode + known sid via stdin payload."""
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.delenv("LORE_CURATOR_MODE", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    yield


def _stdin_with_session(monkeypatch, sid: str) -> None:
    """Mock stdin so `_read_hook_payload` publishes ``sid`` as CLAUDE_SESSION_ID."""
    payload = {"session_id": sid, "hook_event_name": "SessionStart"}
    stream = io.StringIO(json.dumps(payload))
    stream.isatty = lambda: False  # type: ignore[method-assign]
    monkeypatch.setattr("sys.stdin", stream)


def test_session_start_noop_when_off(monkeypatch: pytest.MonkeyPatch) -> None:
    sid = "sid-off-1"
    _stdin_with_session(monkeypatch, sid)
    toggles.set_off("all", sid)

    mock_session = MagicMock()
    with patch("lore_cli.hooks._session_start", mock_session):
        from lore_cli.hooks import cmd_session_start
        cmd_session_start(cwd="/tmp", plain=True, probe=False)

    mock_session.assert_not_called()


def test_session_start_runs_when_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sanity: with no sentinel, the hook does its normal work."""
    sid = "sid-on-1"
    _stdin_with_session(monkeypatch, sid)
    # Sentinel deliberately not set.

    mock_session = MagicMock(return_value="banner")
    with patch("lore_cli.hooks._session_start", mock_session), \
         patch("lore_cli.hooks._emit"):
        from lore_cli.hooks import cmd_session_start
        cmd_session_start(cwd="/tmp", plain=True, probe=True)

    mock_session.assert_called()


def test_pre_compact_noop_when_off(monkeypatch: pytest.MonkeyPatch) -> None:
    sid = "sid-off-2"
    _stdin_with_session(monkeypatch, sid)
    toggles.set_off("all", sid)

    mock_pre = MagicMock()
    with patch("lore_cli.hooks._pre_compact", mock_pre):
        from lore_cli.hooks import cmd_pre_compact
        cmd_pre_compact(cwd="/tmp", plain=True)

    mock_pre.assert_not_called()


def test_stop_noop_when_off(monkeypatch: pytest.MonkeyPatch) -> None:
    sid = "sid-off-3"
    _stdin_with_session(monkeypatch, sid)
    toggles.set_off("all", sid)

    mock_stop = MagicMock()
    with patch("lore_cli.hooks._stop", mock_stop):
        from lore_cli.hooks import cmd_stop
        cmd_stop(plain=True)

    mock_stop.assert_not_called()


def test_user_prompt_submit_noop_when_off(monkeypatch: pytest.MonkeyPatch) -> None:
    sid = "sid-off-4"
    _stdin_with_session(monkeypatch, sid)
    toggles.set_off("all", sid)

    # If short-circuit fails, `_heartbeat` would be called with a real Path.
    mock_hb = MagicMock(return_value=("", ""))
    mock_resolve = MagicMock(return_value=None)
    with patch("lore_cli.hooks._heartbeat", mock_hb), \
         patch("lore_cli.hooks.resolve_scope", mock_resolve):
        from lore_cli.hooks import cmd_user_prompt_submit
        cmd_user_prompt_submit(cwd="/tmp", plain=True)

    mock_hb.assert_not_called()
    mock_resolve.assert_not_called()


def test_off_one_session_does_not_affect_another(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sentinel for sid-A must not silence the hook for sid-B."""
    toggles.set_off("all", "sid-A")
    _stdin_with_session(monkeypatch, "sid-B")

    mock_session = MagicMock(return_value="banner")
    with patch("lore_cli.hooks._session_start", mock_session), \
         patch("lore_cli.hooks._emit"):
        from lore_cli.hooks import cmd_session_start
        cmd_session_start(cwd="/tmp", plain=True, probe=True)

    mock_session.assert_called()


def test_off_with_no_sid_in_env_runs_normally(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the payload has no session_id we can't scope a sentinel; hook proceeds."""
    # Stdin returns empty payload — no CLAUDE_SESSION_ID published.
    stream = io.StringIO("")
    stream.isatty = lambda: False  # type: ignore[method-assign]
    monkeypatch.setattr("sys.stdin", stream)

    mock_session = MagicMock(return_value="banner")
    with patch("lore_cli.hooks._session_start", mock_session), \
         patch("lore_cli.hooks._emit"):
        from lore_cli.hooks import cmd_session_start
        cmd_session_start(cwd="/tmp", plain=True, probe=True)

    mock_session.assert_called()


def test_capture_noop_when_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """SessionEnd capture must short-circuit — it's the only hook bound to
    SessionEnd, and it spawns curators that write to the vault. If unguarded,
    `/lore:off all` could not stop curator-written notes from appearing in the
    next session's SessionStart context (code-reviewer critical finding).
    """
    sid = "sid-cap-1"
    _stdin_with_session(monkeypatch, sid)
    toggles.set_off("all", sid)

    # `_resolve_cwd_capture` is the first thing called after the (currently
    # missing) toggle check. If short-circuit fails, this gets invoked.
    mock_resolve = MagicMock()
    mock_logger = MagicMock()
    with patch("lore_cli.hooks._resolve_cwd_capture", mock_resolve), \
         patch("lore_cli.hooks.HookEventLogger", mock_logger):
        from lore_cli.hooks import capture
        capture(event="session-end")

    mock_resolve.assert_not_called()
    mock_logger.assert_not_called()
