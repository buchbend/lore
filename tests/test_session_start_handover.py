"""Tests for the SessionStart handover-poll for buffer-flush stubs."""
from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lore_core.types import Turn
from lore_core.wiki_config import WikiConfig
from lore_cli.hooks import _poll_buffer_handover
from lore_curator import stub_note
from lore_curator.buffer_append import append_chunk


def _make_turns(n: int = 2) -> list[Turn]:
    return [
        Turn(index=i, timestamp=None, role="user" if i % 2 == 0 else "assistant", text="x")
        for i in range(n)
    ]


@pytest.fixture
def lore_root(tmp_path: Path) -> Path:
    (tmp_path / ".lore" / "buffers").mkdir(parents=True)
    (tmp_path / "wiki" / "private").mkdir(parents=True)
    return tmp_path


@pytest.fixture(autouse=True)
def patch_collectors(monkeypatch):
    monkeypatch.setattr("lore_curator.session_activity.collect_commits_by_sha", lambda *a, **kw: [])
    monkeypatch.setattr("lore_curator.session_activity.collect_issues_in_window", lambda *a, **kw: ([], []))
    monkeypatch.setattr("lore_curator.session_activity.collect_plans_advanced", lambda **kw: [])
    monkeypatch.setattr("lore_curator.session_activity.collect_projects_for_session", lambda **kw: [])
    monkeypatch.setattr("lore_core.git.git_repo_root", lambda cwd: None)
    monkeypatch.setattr("lore_core.git.current_repo", lambda cwd: "")


def _seed_buffer_with_flush_request(
    lore_root: Path, monkeypatch, *, cwd: Path, files=None,
) -> tuple:
    """Seed an accumulating buffer + stub + flush_requested marker."""
    files = files or ["/repo/x.py"]
    monkeypatch.setattr(
        "lore_curator.buffer_append._files_touched_from_turns",
        lambda turns: list(files),
    )
    work_time = datetime(2026, 5, 1, 14, 32, tzinfo=UTC)

    from lore_core.types import Scope, TranscriptHandle

    scope = Scope(
        wiki="private",
        scope="proj:x",
        backend="none",
        claude_md_path=Path("/tmp/CLAUDE.md"),
    )
    handle = TranscriptHandle(
        integration="claude-code",
        id="transcript-X",
        path=Path("/tmp/t.jsonl"),
        cwd=cwd,
        mtime=datetime.now(UTC),
    )
    outcome = append_chunk(
        lore_root=lore_root, chunk_turns=_make_turns(2), local_date="2026-05-01",
        transcript_id="abc", integration="claude-code",
        wiki="private", scope="proj:x",
        cwd=cwd, wiki_root=lore_root / "wiki" / "private",
        cfg=WikiConfig(),
    )
    stub_note.write_or_update(
        outcome=outcome, scope=scope, transcript=handle,
        wiki_root=lore_root / "wiki" / "private",
        work_time=work_time, now=work_time, integration="claude-code",
        chunk_from_hash="h0", chunk_to_hash="h1",
    )
    # Mark the buffer as flush_requested + ready (what SessionEnd would do).
    from lore_curator.buffer_store import FlushRequest
    with outcome.buffer.with_lock():
        outcome.buffer.transition(
            "ready",
            flush_requested=FlushRequest(
                trigger="session-end",
                requested_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                by_pid=1,
            ),
        )
    return outcome.buffer


def test_no_pending_flush_returns_empty(lore_root, monkeypatch):
    cwd = lore_root / "project"
    cwd.mkdir()
    lines = _poll_buffer_handover(lore_root, cwd, timeout_s=0.2)
    assert lines == []


def test_already_closed_buffer_yields_wikilink(lore_root, monkeypatch):
    cwd = lore_root / "project"
    cwd.mkdir()
    buf = _seed_buffer_with_flush_request(lore_root, monkeypatch, cwd=cwd)
    # Pre-close the buffer the way Phase 1 of flush does.
    with buf.with_lock():
        buf.transition("flushing")
        buf.transition("closed")

    lines = _poll_buffer_handover(lore_root, cwd, timeout_s=0.5)
    assert lines and "Picked up" in lines[0]
    assert "[[" in lines[0]


def test_timeout_emits_handover_message(lore_root, monkeypatch):
    cwd = lore_root / "project"
    cwd.mkdir()
    _seed_buffer_with_flush_request(lore_root, monkeypatch, cwd=cwd)

    t0 = time.monotonic()
    lines = _poll_buffer_handover(lore_root, cwd, timeout_s=0.3)
    elapsed = time.monotonic() - t0
    # Polled the full budget.
    assert elapsed >= 0.25
    assert any("still being synthesised" in line for line in lines)


def test_handover_unblocks_when_phase1_lands_during_poll(lore_root, monkeypatch):
    cwd = lore_root / "project"
    cwd.mkdir()
    buf = _seed_buffer_with_flush_request(lore_root, monkeypatch, cwd=cwd)

    def _close_after_delay():
        time.sleep(0.15)
        with buf.with_lock():
            buf.transition("flushing")
            buf.transition("closed")

    t = threading.Thread(target=_close_after_delay, daemon=True)
    t.start()
    lines = _poll_buffer_handover(lore_root, cwd, timeout_s=2.0)
    t.join(timeout=2)
    assert lines and "Picked up" in lines[0]


def test_handover_excludes_unrelated_cwd_and_old_transcripts(lore_root, monkeypatch):
    other_cwd = lore_root / "other"
    other_cwd.mkdir()
    buf = _seed_buffer_with_flush_request(lore_root, monkeypatch, cwd=other_cwd)
    # Backdate so it's neither a cwd match nor "recent".
    from datetime import timedelta
    old = (datetime.now(UTC) - timedelta(hours=10)).isoformat().replace("+00:00", "Z")
    with buf.with_lock():
        buf.patch(last_appended_at=old)

    here = lore_root / "project"
    here.mkdir()
    lines = _poll_buffer_handover(lore_root, here, timeout_s=0.2)
    assert lines == []
