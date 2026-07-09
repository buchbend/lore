"""Integration: two authors' first heartbeats racing across two clones of
one wiki repo (issue #179, AC3).

Composes already-tested primitives — ``ensure_note``'s collision-free path
claim (AC1) and ``auto_push``'s fetch/merge/retry (AC2) — through the real
flush-time wiring (``maybe_auto_commit``) rather than adding new machinery.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from lore_core.session_writer import FiledNote
from lore_core.types import Scope, TranscriptHandle, Turn
from lore_core.wiki_config import WikiConfig
from lore_curator._auto_commit import maybe_auto_commit
from lore_curator.buffer_append import append_chunk
from lore_curator.session_note import ensure_note

_NOW = datetime(2026, 5, 1, 9, 30, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _patch_collectors(monkeypatch):
    monkeypatch.setattr("lore_curator.session_activity.collect_commits_by_sha", lambda *a, **k: [])
    monkeypatch.setattr(
        "lore_curator.session_activity.collect_issues_in_window", lambda *a, **k: ([], [])
    )
    monkeypatch.setattr(
        "lore_curator.session_activity.collect_projects_for_session", lambda **k: []
    )
    monkeypatch.setattr("lore_core.git.git_repo_root", lambda cwd: None)
    monkeypatch.setattr("lore_core.git.current_repo", lambda cwd: "")


class _RecordingLogger:
    """Fake ``RunLogger`` — the only channel a conflict/failure would reach
    the user through, since ``maybe_auto_commit`` never raises."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, record_type: str, **fields) -> None:
        self.events.append((record_type, fields))


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


def _init_bare(path: Path) -> None:
    path.mkdir(parents=True)
    _git(path, "init", "--bare", "--initial-branch=main")


def _init_clone(origin: Path, dest: Path, name: str) -> None:
    _git(dest.parent, "clone", str(origin), str(dest))
    _git(dest, "config", "user.email", f"{name}@example.com")
    _git(dest, "config", "user.name", name)


def _turns(n: int = 3) -> list[Turn]:
    return [
        Turn(index=i, timestamp=None, role="user" if i % 2 == 0 else "assistant", text=f"t{i}")
        for i in range(n)
    ]


def _write_note(host: Path, lore_root: Path, *, handle: str, tid: str, scope_label: str) -> Path:
    """One author's first heartbeat: compose + file a session note."""
    (lore_root / ".lore" / "buffers").mkdir(parents=True)
    outcome = append_chunk(
        lore_root=lore_root,
        chunk_turns=_turns(),
        local_date="2026-05-01",
        transcript_id=tid,
        integration="claude-code",
        wiki="private",
        scope=scope_label,
        cwd=lore_root,
        wiki_root=host,
        cfg=WikiConfig(),
    )
    scope = Scope(
        wiki="private", scope=scope_label, backend="none", claude_md_path=Path("/nonexistent")
    )
    transcript = TranscriptHandle(
        integration="claude-code", id=tid, path=lore_root / "t.jsonl", cwd=lore_root, mtime=_NOW
    )
    result = ensure_note(
        outcome=outcome,
        scope=scope,
        transcript=transcript,
        wiki_root=host,
        work_time=_NOW,
        handle_label=handle,
        integration="claude-code",
    )
    assert result is not None
    return result.path


def test_two_writers_interleaved_flushes_no_lost_notes_no_surfaced_conflicts(
    tmp_path: Path,
) -> None:
    origin = tmp_path / "origin.git"
    host_a = tmp_path / "host_a"
    host_b = tmp_path / "host_b"
    _init_bare(origin)
    _init_clone(origin, host_a, "alice")
    (host_a / ".lore-wiki.yml").write_text("git:\n  auto_commit: true\n  auto_push: true\n")
    _git(host_a, "add", ".lore-wiki.yml")
    _git(host_a, "commit", "-m", "configure auto sync")
    _git(host_a, "push", "-u", "origin", "main")
    _init_clone(origin, host_b, "bob")

    # Two authors' first heartbeats — different scopes, so their
    # deterministically-derived slugs differ, matching the "pre-pull
    # eliminates" same-slug collision the sync layer documents as rare.
    alice_path = _write_note(
        host_a, tmp_path / "lore_a", handle="alice", tid="alice-1", scope_label="proj:x"
    )
    bob_path = _write_note(
        host_b, tmp_path / "lore_b", handle="bob", tid="bob-1", scope_label="proj:y"
    )

    log_a, log_b = _RecordingLogger(), _RecordingLogger()
    maybe_auto_commit(host_a, FiledNote(path=alice_path, wikilink="[[a]]", was_merge=False), log_a)
    # Bob's push races against alice's already-pushed commit — forces the
    # fetch/merge/retry path (AC2) with a real note file (not a synthetic
    # fixture), interleaved with alice's flush (AC3).
    maybe_auto_commit(host_b, FiledNote(path=bob_path, wikilink="[[b]]", was_merge=False), log_b)

    assert [e for e in log_a.events if e[0] == "warning"] == []
    assert [e for e in log_b.events if e[0] == "warning"] == []

    # Both notes converge on both hosts — nothing lost, nothing clobbered.
    _git(host_a, "pull")
    assert alice_path.exists()
    assert (host_a / bob_path.relative_to(host_b)).exists()
    assert (host_b / alice_path.relative_to(host_a)).exists()
    assert bob_path.exists()
