"""Tests for _cross_scope_breadcrumbs — cross-wiki activity at SessionStart."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from lore_core.drain import DrainStore, SYSTEM_SESSION

from lore_core.session_start import cross_scope_breadcrumbs as _cross_scope_breadcrumbs


def _emit_event(lore_root: Path, event: str, wiki: str) -> None:
    """Plant a wiki-tagged row in `_system`. Events that Change C blocks
    from `_system` get written raw so the reader still sees them — the
    cross-scope counter is event-type-agnostic, so the test intent
    (per-wiki activity counts) is preserved."""
    store = DrainStore(lore_root, SYSTEM_SESSION)
    if event == "transcript-synced":
        store.emit("transcript-synced", wiki=wiki, transcript_id="t")
        return
    # Post-#188 drain rows live on the spine; write a raw source="drain"
    # envelope (bypassing the _system emit guard) so the reader sees it.
    spine = lore_root / ".lore" / "spine.jsonl"
    spine.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "v": 1,
        "source": "drain",
        "event": event,
        "level": "info",
        "trace_id": None,
        "wiki": wiki,
        "session_id": SYSTEM_SESSION,
        "run_id": None,
        "scope": None,
        "error_code": None,
        "data": {},
    }
    with open(spine, "a") as f:
        f.write(json.dumps(record) + "\n")


def _emit_old_event(lore_root: Path, event: str, wiki: str, ts: datetime) -> None:
    """Write a drain event with a specific timestamp (bypassing DrainStore.emit)."""
    # Post-#188 drain rows live on the spine; write a raw source="drain"
    # envelope (bypassing the _system emit guard) so the reader sees it.
    spine = lore_root / ".lore" / "spine.jsonl"
    spine.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": ts.isoformat().replace("+00:00", "Z"),
        "v": 1,
        "source": "drain",
        "event": event,
        "level": "info",
        "trace_id": None,
        "wiki": wiki,
        "session_id": SYSTEM_SESSION,
        "run_id": None,
        "scope": None,
        "error_code": None,
        "data": {},
    }
    with open(spine, "a") as f:
        f.write(json.dumps(record) + "\n")


def test_no_other_wiki_events(tmp_path: Path) -> None:
    _emit_event(tmp_path, "note-filed", "private")
    assert _cross_scope_breadcrumbs(tmp_path, "private") == []


def test_shows_other_wiki_activity(tmp_path: Path) -> None:
    _emit_event(tmp_path, "note-filed", "ccat")
    _emit_event(tmp_path, "note-appended", "ccat")

    result = _cross_scope_breadcrumbs(tmp_path, "private")
    assert len(result) == 1
    assert "ccat" in result[0]
    assert "2" in result[0]


def test_excludes_current_wiki(tmp_path: Path) -> None:
    _emit_event(tmp_path, "note-filed", "private")
    _emit_event(tmp_path, "note-filed", "ccat")

    result = _cross_scope_breadcrumbs(tmp_path, "private")
    assert len(result) == 1
    assert "ccat" in result[0]
    assert "private" not in result[0]


def test_multiple_other_wikis(tmp_path: Path) -> None:
    _emit_event(tmp_path, "note-filed", "ccat")
    _emit_event(tmp_path, "note-filed", "docs")

    result = _cross_scope_breadcrumbs(tmp_path, "private")
    assert len(result) == 2
    wikis_mentioned = " ".join(result)
    assert "ccat" in wikis_mentioned
    assert "docs" in wikis_mentioned


def test_ignores_old_events(tmp_path: Path) -> None:
    old = datetime.now(UTC) - timedelta(hours=25)
    _emit_old_event(tmp_path, "note-filed", "ccat", ts=old)

    assert _cross_scope_breadcrumbs(tmp_path, "private") == []


def test_empty_drain(tmp_path: Path) -> None:
    assert _cross_scope_breadcrumbs(tmp_path, "private") == []
