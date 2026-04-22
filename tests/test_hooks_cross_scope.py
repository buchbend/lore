"""Tests for _cross_scope_breadcrumbs — cross-wiki activity at SessionStart."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from lore_core.drain import DrainStore, SYSTEM_SESSION

from lore_cli.hooks import _cross_scope_breadcrumbs


def _emit_event(lore_root: Path, event: str, wiki: str) -> None:
    store = DrainStore(lore_root, SYSTEM_SESSION)
    store.emit(event=event, wiki=wiki)


def _emit_old_event(lore_root: Path, event: str, wiki: str, ts: datetime) -> None:
    """Write a drain event with a specific timestamp (bypassing DrainStore.emit)."""
    drain_dir = lore_root / ".lore" / "drain"
    drain_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": ts.isoformat(),
        "event": event,
        "wiki": wiki,
        "session_id": SYSTEM_SESSION,
        "data": {},
    }
    path = drain_dir / f"{SYSTEM_SESSION}.jsonl"
    with open(path, "a") as f:
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
