"""Issue #52: ``_resolve_active_part`` must scan ``_done/`` for archived parts.

Without this, a closed Part-N (cap-trip / reaper) silently leads to a
duplicate Part-1 stub on the next heartbeat — three lore-scoped notes
all referencing the same ``buffer_stem`` with ``part_index: 1`` was the
user-visible symptom on 2026-05-07.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lore_core.types import Turn
from lore_core.wiki_config import WikiConfig
from lore_curator.buffer_append import append_chunk
from lore_curator.buffer_store import Buffer, BufferTransitionError, done_dir
from lore_curator.session_curator import _resolve_active_part


@pytest.fixture
def lore_root(tmp_path: Path) -> Path:
    (tmp_path / ".lore" / "buffers").mkdir(parents=True)
    return tmp_path


@pytest.fixture(autouse=True)
def patch_collectors(monkeypatch):
    monkeypatch.setattr("lore_curator.session_activity.collect_commits_by_sha", lambda *a, **kw: [])
    monkeypatch.setattr("lore_curator.session_activity.collect_issues_in_window", lambda *a, **kw: ([], []))
    monkeypatch.setattr("lore_curator.session_activity.collect_projects_for_session", lambda **kw: [])
    monkeypatch.setattr("lore_core.git.git_repo_root", lambda cwd: None)
    monkeypatch.setattr("lore_core.git.current_repo", lambda cwd: "")


def _seed_one_buffer(lore_root: Path, *, transcript_id: str = "abc") -> Buffer:
    outcome = append_chunk(
        lore_root=lore_root,
        chunk_turns=[Turn(index=0, timestamp=None, role="user", text="x")],
        local_date="2026-05-01",
        transcript_id=transcript_id,
        integration="claude-code",
        wiki="private",
        scope="proj:x",
        cwd=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        cfg=WikiConfig(),
    )
    return outcome.buffer


def test_resolve_after_done_part1_returns_part2(lore_root):
    """A Part-1 sidecar archived to ``_done/`` (no live buffer) must
    yield ``(2, <prior_stem>)`` — not ``(1, None)``."""
    buf = _seed_one_buffer(lore_root)

    # Move sidecar+log into _done/ as if cap-trip / reaper had closed it.
    with buf.with_lock():
        buf.transition("ready")
        buf.transition("flushing")
        buf.transition("closed")
        moved = buf.close()
    assert moved is not None  # sidecar+log archived

    part_index, prev = _resolve_active_part(
        lore_root, transcript_id="abc", local_date="2026-05-01",
    )
    assert part_index == 2
    assert prev == buf.stem


def test_resolve_skips_unrelated_done_entries(lore_root):
    """``_done/`` entries for a *different* (transcript_id, local_date)
    must NOT bump the part_index for our pair."""
    buf_other = _seed_one_buffer(lore_root, transcript_id="other-id")
    with buf_other.with_lock():
        buf_other.transition("ready")
        buf_other.transition("flushing")
        buf_other.transition("closed")
        buf_other.close()

    part_index, prev = _resolve_active_part(
        lore_root, transcript_id="abc", local_date="2026-05-01",
    )
    assert part_index == 1
    assert prev is None


def test_resolve_skips_done_entry_from_different_local_date(lore_root):
    """Same transcript-id but a different local_date must not bump."""
    outcome = append_chunk(
        lore_root=lore_root,
        chunk_turns=[Turn(index=0, timestamp=None, role="user", text="x")],
        local_date="2026-04-30",
        transcript_id="abc",
        integration="claude-code",
        wiki="private",
        scope="proj:x",
        cwd=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        cfg=WikiConfig(),
    )
    buf = outcome.buffer
    with buf.with_lock():
        buf.transition("ready")
        buf.transition("flushing")
        buf.transition("closed")
        buf.close()

    part_index, prev = _resolve_active_part(
        lore_root, transcript_id="abc", local_date="2026-05-01",
    )
    assert part_index == 1
    assert prev is None


def test_close_refuses_to_overwrite_existing_done_archive(lore_root):
    """``Buffer.close`` must raise rather than silently clobber an
    existing ``_done/<stem>.state.json`` — silent overwrite was masking
    the part-resolution bug."""
    # Pre-seed a _done sidecar with the same stem.
    buf = _seed_one_buffer(lore_root)
    target_dir = done_dir(lore_root)
    pre_existing = target_dir / f"{buf.stem}.state.json"
    pre_existing.write_text(json.dumps({"transcript_id": "abc", "local_date": "2026-05-01"}))
    pre_existing_bytes = pre_existing.read_bytes()

    with buf.with_lock():
        buf.transition("ready")
        buf.transition("flushing")
        buf.transition("closed")
        with pytest.raises(BufferTransitionError, match="refusing to overwrite"):
            buf.close()

    # Pre-existing archived sidecar is byte-for-byte unchanged.
    assert pre_existing.read_bytes() == pre_existing_bytes
