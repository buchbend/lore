"""Tests for P5b — SessionStart drain banner lines.

`_render_drain_lines(lore_root, cwd)` inspects the current session's
drain plus the `_system` drain and produces zero, one, or two banner
lines ("· This session ..." / "· Since you left ...").
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lore_cli.hooks import _render_drain_lines
from lore_core.drain import SYSTEM_SESSION, DrainStore


@pytest.fixture()
def pid_session(monkeypatch):
    """Force `resolve_session_id` down the pid-fallback arm so the session
    id is stable across `resolve_session_id` calls within one test."""
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/nonexistent-home")))
    return f"pid-{os.getpid()}"


def test_render_returns_empty_when_no_events(tmp_path, pid_session):
    lines = _render_drain_lines(tmp_path, tmp_path)
    assert lines == []


def test_render_this_session_line_after_note_filed(tmp_path, pid_session):
    store = DrainStore(tmp_path, pid_session)
    store.emit("note-filed", wiki="ccat", wikilink="[[2026-04-22-foo]]")

    lines = _render_drain_lines(tmp_path, tmp_path)
    assert len(lines) == 1
    assert "This session" in lines[0]
    assert "[[2026-04-22-foo]]" in lines[0]


def test_render_since_you_left_line_from_system_drain(tmp_path, pid_session):
    system = DrainStore(tmp_path, SYSTEM_SESSION)
    system.emit("surface-proposed", wiki="ccat")

    lines = _render_drain_lines(tmp_path, tmp_path)
    assert len(lines) == 1
    assert "Since you left" in lines[0]
    assert "1 surface proposed" in lines[0]


def test_transcript_synced_only_produces_no_drain_line(tmp_path, pid_session):
    """transcript-synced is internal bookkeeping — not surfaced."""
    system = DrainStore(tmp_path, SYSTEM_SESSION)
    system.emit("transcript-synced", wiki="ccat", transcript_id="u1")
    system.emit("transcript-synced", wiki="ccat", transcript_id="u2")

    lines = _render_drain_lines(tmp_path, tmp_path)
    assert lines == []


def test_render_both_lines_when_both_streams_have_events(tmp_path, pid_session):
    session_store = DrainStore(tmp_path, pid_session)
    system_store = DrainStore(tmp_path, SYSTEM_SESSION)
    session_store.emit("note-filed", wiki="a", wikilink="[[n]]")
    system_store.emit("note-filed", wiki="a", wikilink="[[m]]")

    lines = _render_drain_lines(tmp_path, tmp_path)
    assert len(lines) == 2
    assert lines[0].lstrip().startswith("· This session")
    assert lines[1].lstrip().startswith("· Since you left")


def test_render_pluralizes_multiple_new_notes(tmp_path, pid_session):
    session_store = DrainStore(tmp_path, pid_session)
    for i in range(3):
        session_store.emit("note-filed", wiki="a", wikilink=f"[[n{i}]]")
    lines = _render_drain_lines(tmp_path, tmp_path)
    assert "3 new notes" in lines[0]


def test_render_advances_cursor_so_second_call_is_silent(tmp_path, pid_session):
    session_store = DrainStore(tmp_path, pid_session)
    session_store.emit("note-filed", wiki="a", wikilink="[[n]]")

    first = _render_drain_lines(tmp_path, tmp_path)
    assert len(first) == 1
    # A repeat SessionStart in the same Claude session should not
    # re-surface the same events.
    second = _render_drain_lines(tmp_path, tmp_path)
    assert second == []


def test_render_appended_line_names_target_note(tmp_path, pid_session):
    session_store = DrainStore(tmp_path, pid_session)
    session_store.emit("note-appended", wiki="a", wikilink="[[todays-work]]")
    lines = _render_drain_lines(tmp_path, tmp_path)
    assert len(lines) == 1
    assert "added to [[todays-work]]" in lines[0]


def test_render_surfaces_only_events_after_session_cursor(tmp_path, pid_session):
    store = DrainStore(tmp_path, pid_session)
    store.emit("note-filed", wiki="a", wikilink="[[old]]")

    # Advance cursor to "now" so the first event is already behind us
    store.write_cursor(datetime.now(UTC))
    import time; time.sleep(0.02)

    store.emit("note-filed", wiki="a", wikilink="[[new]]")

    lines = _render_drain_lines(tmp_path, tmp_path)
    assert len(lines) == 1
    assert "[[new]]" in lines[0]
    assert "[[old]]" not in lines[0]
