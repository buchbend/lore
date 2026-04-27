"""Hook stdin payload publishes ``CLAUDE_SESSION_ID`` (issue #29).

Reading Claude Code's hook payload from stdin and exporting
``session_id`` as ``CLAUDE_SESSION_ID`` is what keeps the curator
subprocess and the user-prompt-submit heartbeat agreeing on the same
drain file. Without that, mid-stream curator-filed notes are written
to one sid's drain and read from another's — the bug behind #29.
"""

from __future__ import annotations

import io
import json
import os

import pytest

from lore_cli.hooks import _read_hook_payload


@pytest.fixture(autouse=True)
def _scrub_session_id(monkeypatch):
    """Each test starts with no inherited CLAUDE_SESSION_ID."""
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)


def _stdin_with(monkeypatch, body: str) -> None:
    """Replace stdin with a non-tty stream containing ``body``."""
    stream = io.StringIO(body)
    stream.isatty = lambda: False  # type: ignore[method-assign]
    monkeypatch.setattr("sys.stdin", stream)


def test_payload_publishes_session_id(monkeypatch) -> None:
    payload = {"session_id": "abc-123", "cwd": "/tmp", "hook_event_name": "UserPromptSubmit"}
    _stdin_with(monkeypatch, json.dumps(payload))

    out = _read_hook_payload()

    assert out == payload
    assert os.environ["CLAUDE_SESSION_ID"] == "abc-123"


def test_payload_does_not_clobber_existing_session_id(monkeypatch) -> None:
    monkeypatch.setenv("CLAUDE_SESSION_ID", "preset")
    _stdin_with(monkeypatch, json.dumps({"session_id": "incoming"}))

    _read_hook_payload()

    assert os.environ["CLAUDE_SESSION_ID"] == "preset"


def test_tty_short_circuits(monkeypatch) -> None:
    """Manual ``lore hook ... --plain`` from a terminal must not block on read()."""
    stream = io.StringIO("would block")
    stream.isatty = lambda: True  # type: ignore[method-assign]
    monkeypatch.setattr("sys.stdin", stream)

    assert _read_hook_payload() == {}
    assert "CLAUDE_SESSION_ID" not in os.environ


def test_empty_stdin_no_op(monkeypatch) -> None:
    _stdin_with(monkeypatch, "")
    assert _read_hook_payload() == {}
    assert "CLAUDE_SESSION_ID" not in os.environ


def test_malformed_json_no_op(monkeypatch) -> None:
    """Bad JSON must not raise — hooks are best-effort, never abort the user prompt."""
    _stdin_with(monkeypatch, "{this is not json")
    assert _read_hook_payload() == {}
    assert "CLAUDE_SESSION_ID" not in os.environ


def test_payload_without_session_id_returns_dict(monkeypatch) -> None:
    """A payload missing session_id is still returned; we just don't publish env."""
    _stdin_with(monkeypatch, json.dumps({"cwd": "/tmp"}))
    out = _read_hook_payload()
    assert out == {"cwd": "/tmp"}
    assert "CLAUDE_SESSION_ID" not in os.environ


def test_session_id_propagates_to_resolve_session_id(monkeypatch, tmp_path) -> None:
    """End-to-end: payload sid wins over the transcript-freshness heuristic."""
    from lore_core.drain import resolve_session_id

    _stdin_with(monkeypatch, json.dumps({"session_id": "from-payload"}))
    _read_hook_payload()

    sid, origin = resolve_session_id(tmp_path)
    assert sid == "from-payload"
    assert origin == "env"
