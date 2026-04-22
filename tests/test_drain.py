"""Tests for lore_core.drain — per-session event store + session-id resolver."""
from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lore_core.drain import (
    EVENT_VOCAB,
    MAX_DRAIN_LINE,
    SYSTEM_SESSION,
    DrainEvent,
    DrainStore,
    resolve_session_id,
)


# ---------------------------------------------------------------------------
# DrainStore basics
# ---------------------------------------------------------------------------


def test_drain_store_creates_parent_dir_on_init(tmp_path):
    """A fresh vault has no .lore/drain/ yet; construction must not fail."""
    store = DrainStore(tmp_path, "sess-1")
    assert (tmp_path / ".lore" / "drain").exists()
    # emit should work even before any other drain activity
    store.emit("note-filed", wiki="w", path="/x")
    assert store.path.exists()


def test_drain_store_emit_writes_valid_json_line(tmp_path):
    store = DrainStore(tmp_path, "s1")
    store.emit("note-filed", wiki="ccat", wikilink="[[2026-04-22-foo]]")
    lines = store.path.read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["event"] == "note-filed"
    assert rec["wiki"] == "ccat"
    assert rec["session_id"] == "s1"
    assert rec["data"]["wikilink"] == "[[2026-04-22-foo]]"
    assert "ts" in rec


def test_drain_store_rejects_unknown_event(tmp_path):
    store = DrainStore(tmp_path, "s1")
    with pytest.raises(ValueError, match="unknown drain event"):
        store.emit("something-bogus", wiki="w")


def test_drain_store_caps_line_at_max_size_with_truncation_marker(tmp_path):
    """Oversize data payload → truncated marker, line stays within MAX_DRAIN_LINE."""
    store = DrainStore(tmp_path, "s1")
    huge = "X" * 10_000  # well over the cap
    store.emit("note-filed", wiki="w", huge_blob=huge)

    raw = store.path.read_bytes()
    assert len(raw) <= MAX_DRAIN_LINE, f"line exceeded cap: {len(raw)} bytes"
    rec = json.loads(raw.decode())
    assert rec["truncated"] is True
    # huge_blob must be gone; we still know it existed via truncated_from_keys
    assert "huge_blob" not in rec["data"]
    assert "huge_blob" in rec["data"]["truncated_from_keys"]


def test_drain_store_emit_survives_unwritable_dir(tmp_path, monkeypatch):
    """A drain I/O failure must never crash the caller (telemetry, not correctness)."""
    store = DrainStore(tmp_path, "s1")
    # Simulate write failure
    def boom(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(os, "open", boom)
    store.emit("note-filed", wiki="w")  # must not raise


# ---------------------------------------------------------------------------
# DrainStore.read + filters
# ---------------------------------------------------------------------------


def test_drain_read_returns_chronological_events(tmp_path):
    store = DrainStore(tmp_path, "s1")
    store.emit("note-filed", wiki="a", n=1)
    store.emit("note-appended", wiki="a", n=2)
    store.emit("note-filed", wiki="b", n=3)

    events = store.read()
    assert [e.data["n"] for e in events] == [1, 2, 3]
    assert all(isinstance(e, DrainEvent) for e in events)


def test_drain_read_since_filter(tmp_path):
    store = DrainStore(tmp_path, "s1")
    store.emit("note-filed", wiki="a", n=1)
    # Read the first ts, then emit more after it
    first_ts = store.read()[0].ts
    # Tiny sleep so subsequent events have strictly-later timestamps.
    time.sleep(0.02)
    store.emit("note-filed", wiki="a", n=2)
    store.emit("note-filed", wiki="a", n=3)

    later = store.read(since=first_ts + timedelta(microseconds=1))
    assert [e.data["n"] for e in later] == [2, 3]


def test_drain_read_limit_tails(tmp_path):
    store = DrainStore(tmp_path, "s1")
    for i in range(5):
        store.emit("note-filed", wiki="w", n=i)
    got = store.read(limit=2)
    assert [e.data["n"] for e in got] == [3, 4]


def test_drain_read_skips_malformed_lines(tmp_path):
    store = DrainStore(tmp_path, "s1")
    store.emit("note-filed", wiki="w", n=1)
    # Manually tack on a garbage line + a second good one
    with store.path.open("a") as fp:
        fp.write("NOT JSON\n")
    store.emit("note-filed", wiki="w", n=2)
    got = store.read()
    assert [e.data["n"] for e in got] == [1, 2]


def test_drain_cursor_roundtrip(tmp_path):
    store = DrainStore(tmp_path, "s1")
    assert store.read_cursor() is None
    now = datetime.now(UTC)
    store.write_cursor(now)
    assert store.read_cursor() == now


def test_drain_session_isolation(tmp_path):
    """Two sessions write to disjoint files."""
    a = DrainStore(tmp_path, "sess-a")
    b = DrainStore(tmp_path, "sess-b")
    a.emit("note-filed", wiki="x", marker="A")
    b.emit("note-appended", wiki="x", marker="B")

    # A sees only its own event
    a_events = a.read()
    assert len(a_events) == 1
    assert a_events[0].data["marker"] == "A"
    # B likewise
    b_events = b.read()
    assert len(b_events) == 1
    assert b_events[0].data["marker"] == "B"


def test_drain_system_session_path(tmp_path):
    """The _system session lands at a predictable filename."""
    store = DrainStore(tmp_path, SYSTEM_SESSION)
    store.emit("surface-proposed", wiki="w")
    assert (tmp_path / ".lore" / "drain" / "_system.jsonl").exists()


# ---------------------------------------------------------------------------
# resolve_session_id priority chain
# ---------------------------------------------------------------------------


def test_resolve_session_id_prefers_hook_payload(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_SESSION_ID", "env-sid")
    sid, origin = resolve_session_id(
        tmp_path, hook_payload={"session_id": "hook-sid"}
    )
    assert sid == "hook-sid"
    assert origin == "hook-payload"


def test_resolve_session_id_falls_back_to_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_SESSION_ID", "env-sid")
    sid, origin = resolve_session_id(tmp_path, hook_payload=None)
    assert sid == "env-sid"
    assert origin == "env"


def test_resolve_session_id_uses_transcript_when_fresh(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    # Point Path.home() at tmp_path so we can plant a fresh transcript
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    cwd = tmp_path / "proj"
    cwd.mkdir()
    encoded = str(cwd.resolve()).replace("/", "-")
    projects = tmp_path / ".claude" / "projects" / encoded
    projects.mkdir(parents=True)
    (projects / "uuid-fresh.jsonl").write_text("{}")

    sid, origin = resolve_session_id(cwd)
    assert sid == "uuid-fresh"
    assert origin == "transcript-freshness"


def test_resolve_session_id_ignores_stale_transcript(tmp_path, monkeypatch):
    """Transcript older than 2 minutes → falls through to pid fallback."""
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    cwd = tmp_path / "proj"
    cwd.mkdir()
    encoded = str(cwd.resolve()).replace("/", "-")
    projects = tmp_path / ".claude" / "projects" / encoded
    projects.mkdir(parents=True)
    stale = projects / "uuid-stale.jsonl"
    stale.write_text("{}")
    # Backdate 10 minutes
    old = time.time() - 600
    os.utime(stale, (old, old))

    sid, origin = resolve_session_id(cwd)
    assert origin == "pid-fallback"
    assert sid.startswith("pid-")


def test_resolve_session_id_pid_fallback_when_no_hints(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "no-projects"))
    cwd = tmp_path / "proj"
    cwd.mkdir()
    sid, origin = resolve_session_id(cwd)
    assert sid == f"pid-{os.getpid()}"
    assert origin == "pid-fallback"
