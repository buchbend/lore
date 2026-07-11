"""The heartbeat ensures the append-only note_document note exists.

The buffer-and-flush heartbeat no longer writes a live preview note. It
guarantees the session note file exists (fixed disclaimer + machine-first
frontmatter, zero chapters) and records its path on the buffer sidecar;
chapters are appended later, one per flush. These tests pin that seam.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from lore_core import note_document as nd
from lore_core.types import Scope, TranscriptHandle, Turn
from lore_core.wiki_config import WikiConfig
from lore_curator.buffer_append import append_chunk
from lore_curator.buffer_store import Buffer
from lore_curator.session_note import EnsureResult, ensure_note

_NOW = datetime(2026, 5, 1, 9, 30, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _patch_collectors(monkeypatch):
    monkeypatch.setattr("lore_curator.session_activity.collect_commits_by_sha", lambda *a, **k: [])
    monkeypatch.setattr(
        "lore_curator.session_activity.collect_issues_in_window",
        lambda *a, **k: ([], []),
    )
    monkeypatch.setattr(
        "lore_curator.session_activity.collect_projects_for_session",
        lambda **k: [],
    )
    monkeypatch.setattr("lore_core.git.git_repo_root", lambda cwd: None)
    monkeypatch.setattr("lore_core.git.current_repo", lambda cwd: "")


def _turns(n: int = 3) -> list[Turn]:
    return [
        Turn(index=i, timestamp=None, role="user" if i % 2 == 0 else "assistant", text=f"t{i}")
        for i in range(n)
    ]


def _append(lore_root: Path, tid: str = "abc"):
    wiki_root = lore_root / "wiki" / "private"
    return append_chunk(
        lore_root=lore_root,
        chunk_turns=_turns(3),
        local_date="2026-05-01",
        transcript_id=tid,
        integration="claude-code",
        wiki="private",
        scope="proj:x",
        cwd=lore_root,
        wiki_root=wiki_root,
        cfg=WikiConfig(),
    )


def _scope() -> Scope:
    return Scope(
        wiki="private",
        scope="proj:x",
        backend="none",
        claude_md_path=Path("/nonexistent"),
    )


def _handle(lore_root: Path) -> TranscriptHandle:
    return TranscriptHandle(
        integration="claude-code",
        id="abc",
        path=lore_root / "t.jsonl",
        cwd=lore_root,
        mtime=_NOW,
    )


def test_first_heartbeat_creates_note_document_note(tmp_path):
    (tmp_path / ".lore" / "buffers").mkdir(parents=True)
    wiki_root = tmp_path / "wiki" / "private"
    outcome = _append(tmp_path)

    result = ensure_note(
        outcome=outcome,
        scope=_scope(),
        transcript=_handle(tmp_path),
        wiki_root=wiki_root,
        work_time=_NOW,
        handle_label="",
        integration="claude-code",
    )
    assert isinstance(result, EnsureResult)
    assert result.is_first_write is True
    assert result.path.exists()

    view = nd.read_note(result.path)
    assert view.closed is False
    assert view.chapters == []  # no chapters at heartbeat
    assert nd.DISCLAIMER.split(".")[0] in view.body  # the fixed genre disclaimer travels in-body
    assert view.frontmatter["scope"] == "proj:x"


def test_first_heartbeat_records_linkage_frontmatter(tmp_path):
    (tmp_path / ".lore" / "buffers").mkdir(parents=True)
    wiki_root = tmp_path / "wiki" / "private"
    result = ensure_note(
        outcome=_append(tmp_path),
        scope=_scope(),
        transcript=_handle(tmp_path),
        wiki_root=wiki_root,
        work_time=_NOW,
        handle_label="",
        integration="claude-code",
    )
    view = nd.read_note(result.path)
    assert view.frontmatter["linkage"] == {
        "schema_version": 1,
        "repo": "",
        "branch": "",
        "issues": [],
        "prs": [],
        "epics": [],
        "author": "",
        "trace_id": None,
    }


def test_stub_path_is_stamped_and_reused(tmp_path):
    (tmp_path / ".lore" / "buffers").mkdir(parents=True)
    wiki_root = tmp_path / "wiki" / "private"

    first = ensure_note(
        outcome=_append(tmp_path),
        scope=_scope(),
        transcript=_handle(tmp_path),
        wiki_root=wiki_root,
        work_time=_NOW,
        integration="claude-code",
    )
    buf = Buffer.open(tmp_path, transcript_id="abc", local_date="2026-05-01")
    assert buf.read_sidecar().stub_path == str(first.path)

    # A later heartbeat finds the recorded path and does not create a second note.
    second = ensure_note(
        outcome=_append(tmp_path),
        scope=_scope(),
        transcript=_handle(tmp_path),
        wiki_root=wiki_root,
        work_time=_NOW,
        integration="claude-code",
    )
    assert second.is_first_write is False
    assert second.path == first.path
    notes = list((wiki_root / "sessions").rglob("*.md"))
    assert len(notes) == 1


def test_two_authors_same_minute_same_slug_both_get_notes(tmp_path, monkeypatch):
    """Two authors' first heartbeats racing on an identical slug in the
    same minute must not have the second clobber the first's note.

    Neither is in team mode (no _users.yml), so both land in the same
    flat sessions/ dir — the collision scenario AC1 guards against.
    """
    (tmp_path / ".lore" / "buffers").mkdir(parents=True)
    wiki_root = tmp_path / "wiki" / "private"
    monkeypatch.setattr("lore_curator.session_note._derive_slug", lambda **kw: "fixed-a-bug")

    alice = ensure_note(
        outcome=_append(tmp_path, tid="alice-session"),
        scope=_scope(),
        transcript=_handle(tmp_path),
        wiki_root=wiki_root,
        work_time=_NOW,
        handle_label="alice",
        integration="claude-code",
    )
    bob = ensure_note(
        outcome=_append(tmp_path, tid="bob-session"),
        scope=_scope(),
        transcript=_handle(tmp_path),
        wiki_root=wiki_root,
        work_time=_NOW,
        handle_label="bob",
        integration="claude-code",
    )
    assert alice.path != bob.path
    assert alice.path.exists()
    assert bob.path.exists()
    assert nd.read_note(alice.path).frontmatter["user"] == "alice"
    assert nd.read_note(bob.path).frontmatter["user"] == "bob"


def test_noop_heartbeat_returns_none(tmp_path):
    (tmp_path / ".lore" / "buffers").mkdir(parents=True)
    wiki_root = tmp_path / "wiki" / "private"
    outcome = append_chunk(
        lore_root=tmp_path,
        chunk_turns=[],
        local_date="2026-05-01",
        transcript_id="abc",
        integration="claude-code",
        wiki="private",
        scope="proj:x",
        cwd=tmp_path,
        wiki_root=wiki_root,
        cfg=WikiConfig(),
    )
    assert (
        ensure_note(
            outcome=outcome,
            scope=_scope(),
            transcript=_handle(tmp_path),
            wiki_root=wiki_root,
            work_time=_NOW,
        )
        is None
    )
