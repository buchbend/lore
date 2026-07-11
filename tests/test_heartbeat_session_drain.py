"""Heartbeat reads both system and session-scoped drains.

Mid-session notes filed for THIS session are surfaced alongside
background system events.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from lore_core.drain import SYSTEM_SESSION, DrainStore
from lore_core.wiki_config import HeartbeatConfig, WikiConfig
from lore_cli.hooks import _heartbeat


def _write_legacy_system_row(lore_root: Path, *, event: str, wiki: str,
                              wikilink: str, ts: datetime | None = None) -> None:
    """Bypass DrainStore.emit() to plant a row that pre-Phase-1 code wrote.

    Phase 1 of the drain plan (Change C) forbids non-transcript-synced
    events in `_system`, but production vaults still carry legacy rows
    written before the guard. The reader path must continue to handle
    them gracefully — those tests need to plant exactly that shape.
    """
    # Post-#188 the drain lives on the spine; plant a raw source="drain"
    # envelope (bypassing DrainStore.emit()'s _system guard) so the reader
    # still surfaces a legacy system-stream drain event.
    spine = lore_root / ".lore" / "spine.jsonl"
    spine.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": (ts or datetime.now(UTC)).isoformat().replace("+00:00", "Z"),
        "v": 1,
        "source": "drain",
        "event": event,
        "level": "info",
        "trace_id": None,
        "session_id": SYSTEM_SESSION,
        "run_id": None,
        "wiki": wiki,
        "scope": None,
        "error_code": None,
        "data": {"wikilink": wikilink},
    }
    with spine.open("a") as fp:
        fp.write(json.dumps(record) + "\n")


@pytest.fixture()
def lore_root(tmp_path: Path) -> Path:
    (tmp_path / ".lore" / "drain").mkdir(parents=True)
    return tmp_path


@pytest.fixture()
def wiki_cfg() -> WikiConfig:
    return WikiConfig(heartbeat=HeartbeatConfig(enabled=True, cooldown_s=0, push_context=True))


def test_heartbeat_surfaces_session_drain_events(lore_root, wiki_cfg) -> None:
    session_store = DrainStore(lore_root, "test-session")
    session_store.emit("note-filed", wiki="private", wikilink="[[slug]]")

    with patch("lore_cli.hooks._stamp_within_cooldown", return_value=False), \
         patch("lore_cli.hooks._claude_code_pid", return_value=None), \
         patch("lore_core.drain.resolve_session_id", return_value=("test-session", "test")):
        sys_msg, ctx = _heartbeat(lore_root, lore_root, wiki_cfg, pid=99999)

    assert sys_msg is not None
    assert "new note [[slug]]" in sys_msg
    assert ctx is not None
    assert "[[slug]]" in ctx


def test_heartbeat_merges_system_and_session_events(lore_root, wiki_cfg) -> None:
    # Seed the system cursor in the past so cold-start init does not
    # filter the legacy row we plant below.
    DrainStore(lore_root, SYSTEM_SESSION).write_cursor(
        datetime.now(UTC) - timedelta(hours=1)
    )
    # Legacy data: a stale note-filed row left over before Change C
    # added the producer-side guard. Heartbeat must keep surfacing it
    # so users see existing pollution surface (and can prune it).
    _write_legacy_system_row(
        lore_root, event="note-filed", wiki="private", wikilink="[[sys-note]]",
    )

    session_store = DrainStore(lore_root, "test-session")
    session_store.emit("note-appended", wiki="private", wikilink="[[sess-note]]")

    with patch("lore_cli.hooks._stamp_within_cooldown", return_value=False), \
         patch("lore_cli.hooks._claude_code_pid", return_value=None), \
         patch("lore_core.drain.resolve_session_id", return_value=("test-session", "test")):
        sys_msg, ctx = _heartbeat(lore_root, lore_root, wiki_cfg, pid=99999)

    assert sys_msg is not None
    assert "new note [[sys-note]]" in sys_msg
    assert "added to [[sess-note]]" in sys_msg
    assert ctx is not None
    assert "[[sys-note]]" in ctx
    assert "[[sess-note]]" in ctx


def test_heartbeat_cursors_advance_independently(lore_root, wiki_cfg) -> None:
    DrainStore(lore_root, SYSTEM_SESSION).write_cursor(
        datetime.now(UTC) - timedelta(hours=1)
    )
    # Legacy stale row in _system to verify both streams advance.
    _write_legacy_system_row(
        lore_root, event="note-filed", wiki="private", wikilink="[[s1]]",
    )

    session_store = DrainStore(lore_root, "test-session")
    session_store.emit("note-filed", wiki="private", wikilink="[[n1]]")

    with patch("lore_cli.hooks._stamp_within_cooldown", return_value=False), \
         patch("lore_cli.hooks._claude_code_pid", return_value=None), \
         patch("lore_core.drain.resolve_session_id", return_value=("test-session", "test")):
        sys_msg1, _ = _heartbeat(lore_root, lore_root, wiki_cfg, pid=99999)
        assert sys_msg1 is not None

        # Second call: no new events → None
        sys_msg2, _ = _heartbeat(lore_root, lore_root, wiki_cfg, pid=99999)
        assert sys_msg2 is None


def test_heartbeat_transcript_synced_suppressed(lore_root, wiki_cfg) -> None:
    """transcript-synced is internal — heartbeat should not surface it."""
    system_store = DrainStore(lore_root, SYSTEM_SESSION)
    system_store.emit("transcript-synced", wiki="private", transcript_id="t1")

    with patch("lore_cli.hooks._stamp_within_cooldown", return_value=False), \
         patch("lore_cli.hooks._claude_code_pid", return_value=None), \
         patch("lore_core.drain.resolve_session_id", return_value=("test-session", "test")):
        sys_msg, ctx = _heartbeat(lore_root, lore_root, wiki_cfg, pid=99999)

    assert sys_msg is None
