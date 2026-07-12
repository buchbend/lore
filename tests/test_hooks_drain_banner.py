"""Tests for P5b — SessionStart drain banner lines.

`_render_drain_lines(lore_root, cwd)` inspects the current session's
drain plus the `_system` drain and produces zero, one, or two banner
lines ("· This session ..." / "· Since you left ...").
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lore_core.drain_banner import render_drain_lines as _render_drain_lines
from lore_core.drain import SYSTEM_SESSION, DrainStore


def _seed_system_cursor_in_past(lore_root: Path) -> None:
    """Pre-plant `_system.cursor` so cold-start init does not skip events.

    Phase 2 cold-starts `_system.cursor` to `now`, which would filter
    out any pre-emitted event. Tests that exercise the system-stream
    rendering path need a cursor that predates their fixture events.
    """
    DrainStore(lore_root, SYSTEM_SESSION).write_cursor(
        datetime.now(UTC) - timedelta(hours=1)
    )


def _write_legacy_system_row(lore_root: Path, *, event: str, wiki: str,
                              wikilink: str | None = None,
                              **extra) -> None:
    """Bypass DrainStore.emit() to plant a row that pre-Phase-1 code wrote.

    Phase 1 (Change C) forbids non-transcript-synced events in `_system`,
    but production vaults still carry legacy rows written before the
    guard. The reader path keeps surfacing them so users notice
    pollution and can prune it.
    """
    # Post-#188 the drain lives on the spine; plant a raw source="drain"
    # envelope directly (bypassing DrainStore.emit()'s _system guard) to
    # simulate a system-stream drain event the banner must still surface.
    spine = lore_root / ".lore" / "spine.jsonl"
    spine.parent.mkdir(parents=True, exist_ok=True)
    data = dict(extra)
    if wikilink is not None:
        data["wikilink"] = wikilink
    record = {
        "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
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
        "data": data,
    }
    with spine.open("a") as fp:
        fp.write(json.dumps(record) + "\n")


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
    # Legacy stale row in _system (would-be-blocked by Change C now)
    # still flows through the reader so users see what's there.
    _seed_system_cursor_in_past(tmp_path)
    _write_legacy_system_row(tmp_path, event="surface-proposed", wiki="ccat")

    lines = _render_drain_lines(tmp_path, tmp_path)
    assert len(lines) == 1
    assert "Since you left" in lines[0]
    assert "1 surface proposed" in lines[0]


def test_transcript_synced_only_produces_no_drain_line(tmp_path, pid_session):
    """transcript-synced is internal bookkeeping — not surfaced."""
    _seed_system_cursor_in_past(tmp_path)
    system = DrainStore(tmp_path, SYSTEM_SESSION)
    system.emit("transcript-synced", wiki="ccat", transcript_id="u1")
    system.emit("transcript-synced", wiki="ccat", transcript_id="u2")

    lines = _render_drain_lines(tmp_path, tmp_path)
    assert lines == []


def test_render_both_lines_when_both_streams_have_events(tmp_path, pid_session):
    _seed_system_cursor_in_past(tmp_path)
    session_store = DrainStore(tmp_path, pid_session)
    session_store.emit("note-filed", wiki="a", wikilink="[[n]]")
    _write_legacy_system_row(
        tmp_path, event="note-filed", wiki="a", wikilink="[[m]]",
    )

    lines = _render_drain_lines(tmp_path, tmp_path)
    assert len(lines) == 2
    assert lines[0].lstrip().startswith("· This session")
    assert lines[1].lstrip().startswith("· Since you left")


def test_render_pluralizes_multiple_new_notes(tmp_path, pid_session):
    session_store = DrainStore(tmp_path, pid_session)
    for i in range(3):
        session_store.emit("note-filed", wiki="a", wikilink=f"[[n{i}]]")
    lines = _render_drain_lines(tmp_path, tmp_path)
    assert "3 new notes in a" in lines[0]


def test_render_multi_wiki_new_notes_breakdown(tmp_path, pid_session):
    """2+ notes spanning multiple wikis render a per-wiki tally in parens."""
    session_store = DrainStore(tmp_path, pid_session)
    session_store.emit("note-filed", wiki="a", wikilink="[[n1]]")
    session_store.emit("note-filed", wiki="a", wikilink="[[n2]]")
    session_store.emit("note-filed", wiki="b", wikilink="[[n3]]")
    lines = _render_drain_lines(tmp_path, tmp_path)
    # Highest count first, alphabetical tiebreak.
    assert "3 new notes (2 in a, 1 in b)" in lines[0]


def test_render_multi_appended_named_with_wiki(tmp_path, pid_session):
    """2+ appends collapse to a wiki-tagged count."""
    session_store = DrainStore(tmp_path, pid_session)
    session_store.emit("note-appended", wiki="a", wikilink="[[t1]]")
    session_store.emit("note-appended", wiki="a", wikilink="[[t2]]")
    lines = _render_drain_lines(tmp_path, tmp_path)
    assert "2 added in a" in lines[0]


def test_render_surface_proposed_named_with_wiki(tmp_path, pid_session):
    """surface-proposed gets the same wiki context treatment."""
    _seed_system_cursor_in_past(tmp_path)
    _write_legacy_system_row(tmp_path, event="surface-proposed", wiki="ccat")
    _write_legacy_system_row(tmp_path, event="surface-proposed", wiki="ccat")
    lines = _render_drain_lines(tmp_path, tmp_path)
    assert "2 surface proposed in ccat" in lines[0]


def test_render_falls_back_when_wiki_missing(tmp_path, pid_session):
    """Legacy events without a wiki tag fall back to the bare count."""
    _seed_system_cursor_in_past(tmp_path)
    # Both rows have no wiki — guard against partial tallies.
    _write_legacy_system_row(tmp_path, event="note-filed", wiki=None, wikilink="[[x]]")
    _write_legacy_system_row(tmp_path, event="note-filed", wiki=None, wikilink="[[y]]")
    lines = _render_drain_lines(tmp_path, tmp_path)
    assert "2 new notes" in lines[0]
    assert " in " not in lines[0].split("Since you left")[-1]


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


# ---------------------------------------------------------------------------
# Cold-start: a stale `_system.jsonl` row from a prior install MUST NOT
# resurface on a fresh session. `read_or_init_cursor` plants the cursor
# at `now` on first read, leaving every historical row behind it.
# ---------------------------------------------------------------------------


def test_render_cold_start_skips_stale_system_events(tmp_path, pid_session):
    """No `_system.cursor` + a pre-existing stale row → silent banner."""
    # A "haunting" legacy row, written before the producer-side guard.
    _write_legacy_system_row(
        tmp_path, event="note-filed", wiki="ccat", wikilink="[[debug-test-29-event]]",
    )
    # No system cursor exists.
    sys_cursor = (tmp_path / ".lore" / "drain" / "_system.cursor")
    assert not sys_cursor.exists()

    lines = _render_drain_lines(tmp_path, tmp_path)
    assert lines == []
    # Cursor file is now planted at ~now so we never resurface that row.
    assert sys_cursor.exists()


def test_render_cold_start_then_new_event_surfaces(tmp_path, pid_session):
    """After cold-start init, an event emitted *after* the cursor surfaces."""
    # First call: cold-start init, no events
    first = _render_drain_lines(tmp_path, tmp_path)
    assert first == []

    # A new transcript-synced row arrives — it should NOT surface
    # (transcript-synced is suppressed in the banner) but a legitimate
    # post-cursor system event would surface. Use a legacy raw write
    # (post-cursor ts) to simulate a wiki-tagged system event that
    # `_format_drain_summary` actually renders.
    import time; time.sleep(0.02)
    _write_legacy_system_row(
        tmp_path, event="note-filed", wiki="ccat", wikilink="[[fresh]]",
    )

    second = _render_drain_lines(tmp_path, tmp_path)
    assert len(second) == 1
    assert "Since you left" in second[0]
    assert "[[fresh]]" in second[0]
