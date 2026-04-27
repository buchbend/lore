"""Tests for lore_mcp.reindex_watcher."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from lore_mcp.reindex_watcher import (
    ReindexDirtyState,
    _wiki_for_path,
    start_watcher,
)


pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


# ---------------------------------------------------------------------------
# ReindexDirtyState — pure unit tests, no fs needed
# ---------------------------------------------------------------------------


def test_state_starts_empty() -> None:
    s = ReindexDirtyState()
    assert s.snapshot() == set()
    assert s.is_dirty("private") is False


def test_state_marks_and_reads() -> None:
    s = ReindexDirtyState()
    s.mark_dirty("private")
    assert s.is_dirty("private") is True
    assert s.is_dirty("ccat") is False


def test_state_take_clears_and_returns() -> None:
    s = ReindexDirtyState()
    s.mark_dirty("private")
    assert s.take("private") is True
    assert s.is_dirty("private") is False
    assert s.take("private") is False  # already cleared


def test_wiki_for_path_resolves_first_segment(tmp_path: Path) -> None:
    wiki_root = tmp_path / "wiki"
    (wiki_root / "private" / "concepts").mkdir(parents=True)
    note = wiki_root / "private" / "concepts" / "foo.md"
    assert _wiki_for_path(note, wiki_root) == "private"


def test_wiki_for_path_returns_none_outside(tmp_path: Path) -> None:
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()
    outside = tmp_path / "elsewhere" / "foo.md"
    outside.parent.mkdir()
    outside.write_text("x")
    assert _wiki_for_path(outside, wiki_root) is None


# ---------------------------------------------------------------------------
# start_watcher — needs watchdog; skip cleanly if absent
# ---------------------------------------------------------------------------


watchdog = pytest.importorskip("watchdog")


def _wait_for_dirty(state: ReindexDirtyState, wiki: str, timeout: float = 2.0) -> bool:
    """Poll the dirty state until set, or timeout. Watchdog events are async."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if state.is_dirty(wiki):
            return True
        time.sleep(0.02)
    return False


def test_watcher_marks_dirty_on_md_create(tmp_path: Path) -> None:
    lore_root = tmp_path / "vault"
    wiki = lore_root / "wiki" / "private"
    wiki.mkdir(parents=True)

    state = ReindexDirtyState()
    observer = start_watcher(lore_root, state)
    assert observer is not None
    try:
        # Give the OS observer a moment to start before the first event.
        time.sleep(0.05)
        (wiki / "concepts").mkdir(parents=True)
        (wiki / "concepts" / "foo.md").write_text("hello\n")

        assert _wait_for_dirty(state, "private")
    finally:
        observer.stop()
        observer.join(timeout=1)


def test_watcher_marks_dirty_on_md_modify(tmp_path: Path) -> None:
    lore_root = tmp_path / "vault"
    wiki = lore_root / "wiki" / "private"
    wiki.mkdir(parents=True)
    note = wiki / "foo.md"
    note.write_text("v1\n")

    state = ReindexDirtyState()
    observer = start_watcher(lore_root, state)
    assert observer is not None
    try:
        time.sleep(0.05)
        note.write_text("v2\n")

        assert _wait_for_dirty(state, "private")
    finally:
        observer.stop()
        observer.join(timeout=1)


def test_watcher_ignores_non_md_writes(tmp_path: Path) -> None:
    lore_root = tmp_path / "vault"
    wiki = lore_root / "wiki" / "private"
    wiki.mkdir(parents=True)

    state = ReindexDirtyState()
    observer = start_watcher(lore_root, state)
    assert observer is not None
    try:
        time.sleep(0.05)
        (wiki / "log.txt").write_text("not a note\n")
        (wiki / "data.json").write_text("{}\n")

        # Give the watcher time to (not) react.
        time.sleep(0.2)
        assert state.is_dirty("private") is False
    finally:
        observer.stop()
        observer.join(timeout=1)


def test_watcher_returns_none_when_wiki_dir_missing(tmp_path: Path) -> None:
    """No wiki/ dir → no observer, no exception."""
    lore_root = tmp_path / "empty-vault"
    lore_root.mkdir()
    state = ReindexDirtyState()
    observer = start_watcher(lore_root, state)
    assert observer is None
