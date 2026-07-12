"""Tests for the SessionEnd / PreCompact flush_requested wiring."""
from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lore_core.types import Turn
from lore_core.wiki_config import WikiConfig
from lore_curator.capture_routing import (
    request_flush_for_my_buffers as _request_flush_for_my_buffers,
)
from lore_curator.buffer_append import append_chunk


def _make_turns(n: int = 2) -> list[Turn]:
    return [
        Turn(index=i, timestamp=None, role="user" if i % 2 == 0 else "assistant", text="x")
        for i in range(n)
    ]


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


def _seed(lore_root: Path, *, transcript_id: str = "abc") -> tuple:
    outcome = append_chunk(
        lore_root=lore_root, chunk_turns=_make_turns(2), local_date="2026-05-01",
        transcript_id=transcript_id, integration="claude-code", wiki="private", scope="proj:x",
        cwd=lore_root, wiki_root=lore_root / "wiki" / "private", cfg=WikiConfig(),
    )
    return outcome.buffer


def test_request_flush_marks_my_buffers_in_place_for_session_end(lore_root):
    """session-end / pre-compact route to in_place mode and leave the
    buffer in ``accumulating`` so the same transcript-id+date keeps a
    single note across infrastructure boundaries."""
    buf = _seed(lore_root)
    stamped = _request_flush_for_my_buffers(lore_root, trigger="session-end")
    assert stamped == 1
    sidecar = buf.read_sidecar()
    # State stays at ``accumulating`` — the buffer is still live and may
    # absorb more chunks if the session continues. Only cap-trip and the
    # reaper transition to ``ready``/``closed``.
    assert sidecar.state == "accumulating"
    assert sidecar.flush_requested is not None
    assert sidecar.flush_requested.trigger == "session-end"
    assert sidecar.flush_requested.mode == "in_place"
    assert sidecar.flush_requested.by_pid == os.getpid()


def test_request_flush_pre_compact_routes_in_place(lore_root):
    buf = _seed(lore_root)
    stamped = _request_flush_for_my_buffers(lore_root, trigger="pre-compact")
    assert stamped == 1
    sidecar = buf.read_sidecar()
    assert sidecar.state == "accumulating"
    assert sidecar.flush_requested.mode == "in_place"


def test_request_flush_skips_other_pid(lore_root):
    buf = _seed(lore_root)
    # Forge a different owner.pid so iter_for_pid doesn't match.
    with buf.with_lock():
        from lore_curator.buffer_store import OwnerInfo
        buf.patch(owner=OwnerInfo(
            pid=42, host="other", start_ts=0.0, run_id="", claude_session_id="",
        ))
    stamped = _request_flush_for_my_buffers(lore_root, trigger="session-end")
    assert stamped == 0
    sidecar = buf.read_sidecar()
    assert sidecar.state == "accumulating"
    assert sidecar.flush_requested is None


def test_request_flush_skips_closed_buffers(lore_root):
    buf = _seed(lore_root)
    with buf.with_lock():
        buf.transition("ready")
        buf.transition("flushing")
        buf.transition("closed")
    stamped = _request_flush_for_my_buffers(lore_root, trigger="session-end")
    assert stamped == 0


def test_request_flush_idempotent_when_request_already_stamped(lore_root):
    buf = _seed(lore_root)
    # First call stamps in_place flush_requested.
    _request_flush_for_my_buffers(lore_root, trigger="session-end")
    # Second call -> short-circuits without touching state.
    stamped = _request_flush_for_my_buffers(lore_root, trigger="pre-compact")
    assert stamped == 0
    sidecar = buf.read_sidecar()
    assert sidecar.flush_requested is not None
    assert sidecar.flush_requested.trigger == "session-end"  # original trigger preserved
    assert sidecar.flush_requested.mode == "in_place"


def test_request_flush_max_scan_bound(lore_root):
    for tid in ("a", "b", "c", "d"):
        _seed(lore_root, transcript_id=tid)
    stamped = _request_flush_for_my_buffers(lore_root, trigger="session-end", max_scan=2)
    # Bound is on scan count, not stamp count.
    assert stamped <= 2
