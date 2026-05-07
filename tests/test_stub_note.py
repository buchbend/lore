"""Tests for lore_curator.stub_note — live deterministic stub."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from lore_core.schema import parse_frontmatter
from lore_core.types import Scope, TranscriptHandle, Turn
from lore_core.wiki_config import WikiConfig
from lore_curator import stub_note
from lore_curator.buffer_append import append_chunk
from lore_curator.stub_note import (
    STUB_DESCRIPTION_PLACEHOLDER,
    STUB_SUMMARY_PLACEHOLDER,
    write_or_update,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_scope(scope_str: str = "proj:feature") -> Scope:
    return Scope(
        wiki="private",
        scope=scope_str,
        backend="none",
        claude_md_path=Path("/tmp/CLAUDE.md"),
    )


def _make_handle(transcript_id: str = "transcript-abc") -> TranscriptHandle:
    return TranscriptHandle(
        integration="claude-code",
        id=transcript_id,
        path=Path("/tmp/transcript.jsonl"),
        cwd=Path("/tmp"),
        mtime=datetime.now(UTC),
    )


def _make_turns(n: int = 2) -> list[Turn]:
    return [
        Turn(index=i, timestamp=None, role="user" if i % 2 == 0 else "assistant", text=f"t{i}")
        for i in range(n)
    ]


@pytest.fixture
def lore_root(tmp_path: Path) -> Path:
    (tmp_path / ".lore" / "buffers").mkdir(parents=True)
    (tmp_path / "wiki" / "private").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def patch_collectors(monkeypatch):
    monkeypatch.setattr(
        "lore_curator.session_activity.collect_commits_by_sha",
        lambda *a, **kw: [],
    )
    monkeypatch.setattr(
        "lore_curator.session_activity.collect_issues_in_window",
        lambda *a, **kw: ([], []),
    )
    monkeypatch.setattr(
        "lore_curator.session_activity.collect_projects_for_session",
        lambda **kw: [],
    )
    monkeypatch.setattr(
        "lore_core.git.git_repo_root",
        lambda cwd: None,
    )
    monkeypatch.setattr(
        "lore_core.git.current_repo",
        lambda cwd: "",
    )


def _do_append(lore_root: Path, transcript_id: str = "abc", *,
               turns=None, files_touched=None, files_read=None,
               monkeypatch=None,
               local_date: str = "2026-05-01") -> tuple:
    """Run one heartbeat; return (outcome, chunk_from_hash, chunk_to_hash).

    ``files_touched`` drives both the legacy-union mock and the new
    edits-only mock (``files_modified``) — every path is treated as an
    edit by default, which is the dominant test case. Pass ``files_read``
    explicitly to exercise the read-only / interview-style code paths.
    """
    turns = turns if turns is not None else _make_turns(2)
    if files_touched is not None and monkeypatch is not None:
        monkeypatch.setattr(
            "lore_curator.buffer_append._files_touched_from_turns",
            lambda _turns: list(files_touched),
        )
        monkeypatch.setattr(
            "lore_curator.buffer_append._files_modified_from_turns",
            lambda _turns: list(files_touched),
        )
    if files_read is not None and monkeypatch is not None:
        monkeypatch.setattr(
            "lore_curator.buffer_append._files_read_from_turns",
            lambda _turns: list(files_read),
        )
    outcome = append_chunk(
        lore_root=lore_root,
        chunk_turns=turns,
        local_date=local_date,
        transcript_id=transcript_id,
        integration="claude-code",
        wiki="private",
        scope="proj:feature",
        cwd=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        cfg=WikiConfig(),
    )
    return outcome, turns[0].content_hash(), turns[-1].content_hash()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_first_heartbeat_creates_stub_at_canonical_path(
    lore_root, patch_collectors, monkeypatch,
):
    work_time = datetime(2026, 5, 1, 14, 32, tzinfo=UTC)
    outcome, fh, th = _do_append(
        lore_root,
        files_touched=["/repo/src/auth.py"],
        monkeypatch=monkeypatch,
    )
    result = write_or_update(
        outcome=outcome,
        scope=_make_scope(),
        transcript=_make_handle(),
        wiki_root=lore_root / "wiki" / "private",
        work_time=work_time,
        now=work_time,
        integration="claude-code",
        chunk_from_hash=fh,
        chunk_to_hash=th,
    )
    assert result is not None
    assert result.is_first_write is True
    # Path follows sessions/<YYYY>/<MM>/<DD>-<HHMM>-<slug>.md
    path = result.path
    assert path.exists()
    assert "/sessions/2026/05/01-1432-" in str(path)
    text = path.read_text()
    fm = parse_frontmatter(text)
    assert fm["state"] == "stub"
    assert fm["description"] == STUB_DESCRIPTION_PLACEHOLDER
    assert fm["scope"] == "proj:feature"
    assert STUB_SUMMARY_PLACEHOLDER in text
    # Activity-only body — no narrative bullets, no decisions.
    assert "## Decisions made" not in text
    assert "## What we worked on" not in text
    assert "## Loose ends" not in text


def test_slug_falls_back_to_files_touched_basename(
    lore_root, patch_collectors, monkeypatch,
):
    outcome, fh, th = _do_append(
        lore_root,
        files_touched=["/repo/src/auth_handler.py"],
        monkeypatch=monkeypatch,
    )
    result = write_or_update(
        outcome=outcome,
        scope=_make_scope(),
        transcript=_make_handle(),
        wiki_root=lore_root / "wiki" / "private",
        work_time=datetime(2026, 5, 1, 14, 32, tzinfo=UTC),
        now=datetime(2026, 5, 1, 14, 32, tzinfo=UTC),
        integration="claude-code",
        chunk_from_hash=fh,
        chunk_to_hash=th,
    )
    assert "auth-handler" in result.path.name


def test_slug_falls_back_to_session_scope_hhmm(
    lore_root, patch_collectors, monkeypatch,
):
    """No commits, no files_touched -> slug uses scope+HHMM fallback."""
    outcome, fh, th = _do_append(
        lore_root,
        files_touched=[],
        monkeypatch=monkeypatch,
    )
    result = write_or_update(
        outcome=outcome,
        scope=_make_scope("proj:feature"),
        transcript=_make_handle(),
        wiki_root=lore_root / "wiki" / "private",
        work_time=datetime(2026, 5, 1, 14, 32, tzinfo=UTC),
        now=datetime(2026, 5, 1, 14, 32, tzinfo=UTC),
        integration="claude-code",
        chunk_from_hash=fh,
        chunk_to_hash=th,
    )
    # Fallback slug pattern: session-<scope>-<hhmm>
    assert "session-proj-feature-1432" in result.path.name


def test_subsequent_heartbeat_rewrites_in_place_with_same_path(
    lore_root, patch_collectors, monkeypatch,
):
    work_time = datetime(2026, 5, 1, 14, 32, tzinfo=UTC)
    o1, fh1, th1 = _do_append(
        lore_root,
        files_touched=["/repo/a.py"],
        monkeypatch=monkeypatch,
    )
    r1 = write_or_update(
        outcome=o1, scope=_make_scope(), transcript=_make_handle(),
        wiki_root=lore_root / "wiki" / "private",
        work_time=work_time, now=work_time,
        integration="claude-code",
        chunk_from_hash=fh1, chunk_to_hash=th1,
    )

    # Second heartbeat with new files.
    monkeypatch.setattr(
        "lore_curator.buffer_append._files_touched_from_turns",
        lambda _turns: ["/repo/b.py"],
    )
    monkeypatch.setattr(
        "lore_curator.buffer_append._files_modified_from_turns",
        lambda _turns: ["/repo/b.py"],
    )
    turns_b = [Turn(index=2, timestamp=None, role="user", text="more")]
    outcome2 = append_chunk(
        lore_root=lore_root, chunk_turns=turns_b, local_date="2026-05-01",
        transcript_id="abc", integration="claude-code", wiki="private", scope="proj:feature",
        cwd=lore_root, wiki_root=lore_root / "wiki" / "private", cfg=WikiConfig(),
    )
    r2 = write_or_update(
        outcome=outcome2, scope=_make_scope(), transcript=_make_handle(),
        wiki_root=lore_root / "wiki" / "private",
        work_time=work_time, now=work_time,
        integration="claude-code",
        chunk_from_hash=turns_b[0].content_hash(), chunk_to_hash=turns_b[-1].content_hash(),
    )

    assert r2.path == r1.path
    assert r2.is_first_write is False
    assert r2.skipped is False

    fm = parse_frontmatter(r2.path.read_text())
    # New schema: edits land in ``files_modified``; ``files_touched`` is
    # no longer written by buffer-flush stubs (kept only as a legacy
    # read-side fallback in the merge gate).
    assert sorted(fm["files_modified"]) == ["/repo/a.py", "/repo/b.py"]
    assert "files_touched" not in fm
    # source_transcripts grew per-chunk
    assert len(fm["source_transcripts"]) == 2


def test_unchanged_accumulators_skip_disk_rewrite(
    lore_root, patch_collectors, monkeypatch,
):
    work_time = datetime(2026, 5, 1, 14, 32, tzinfo=UTC)
    o1, fh1, th1 = _do_append(
        lore_root,
        files_touched=["/repo/a.py"],
        monkeypatch=monkeypatch,
    )
    r1 = write_or_update(
        outcome=o1, scope=_make_scope(), transcript=_make_handle(),
        wiki_root=lore_root / "wiki" / "private",
        work_time=work_time, now=work_time,
        integration="claude-code",
        chunk_from_hash=fh1, chunk_to_hash=th1,
    )
    mtime_before = r1.path.stat().st_mtime_ns

    # Second heartbeat: same chunk -> same files; accumulators unchanged.
    o2, fh2, th2 = _do_append(
        lore_root,
        files_touched=["/repo/a.py"],
        monkeypatch=monkeypatch,
        turns=_make_turns(2),
    )
    assert o2.accumulators_unchanged is True
    r2 = write_or_update(
        outcome=o2, scope=_make_scope(), transcript=_make_handle(),
        wiki_root=lore_root / "wiki" / "private",
        work_time=work_time, now=work_time,
        integration="claude-code",
        chunk_from_hash=fh2, chunk_to_hash=th2,
    )
    assert r2.skipped is True
    assert r2.path.stat().st_mtime_ns == mtime_before


def test_slug_never_changes_across_heartbeats(
    lore_root, patch_collectors, monkeypatch,
):
    work_time = datetime(2026, 5, 1, 14, 32, tzinfo=UTC)
    o1, fh1, th1 = _do_append(
        lore_root,
        files_touched=["/repo/auth.py"],
        monkeypatch=monkeypatch,
    )
    r1 = write_or_update(
        outcome=o1, scope=_make_scope(), transcript=_make_handle(),
        wiki_root=lore_root / "wiki" / "private",
        work_time=work_time, now=work_time,
        integration="claude-code",
        chunk_from_hash=fh1, chunk_to_hash=th1,
    )
    original_slug = r1.path.stem
    # New chunk introduces a different filename — should NOT change the slug.
    monkeypatch.setattr(
        "lore_curator.buffer_append._files_touched_from_turns",
        lambda _turns: ["/repo/totally/different/payments.py"],
    )
    monkeypatch.setattr(
        "lore_curator.buffer_append._files_modified_from_turns",
        lambda _turns: ["/repo/totally/different/payments.py"],
    )
    turns_b = [Turn(index=2, timestamp=None, role="user", text="payments work")]
    outcome2 = append_chunk(
        lore_root=lore_root, chunk_turns=turns_b, local_date="2026-05-01",
        transcript_id="abc", integration="claude-code", wiki="private", scope="proj:feature",
        cwd=lore_root, wiki_root=lore_root / "wiki" / "private", cfg=WikiConfig(),
    )
    r2 = write_or_update(
        outcome=outcome2, scope=_make_scope(), transcript=_make_handle(),
        wiki_root=lore_root / "wiki" / "private",
        work_time=work_time, now=work_time,
        integration="claude-code",
        chunk_from_hash=turns_b[0].content_hash(), chunk_to_hash=turns_b[-1].content_hash(),
    )
    assert r2.path.stem == original_slug


def test_stub_path_recorded_in_sidecar_on_first_write(
    lore_root, patch_collectors, monkeypatch,
):
    work_time = datetime(2026, 5, 1, 14, 32, tzinfo=UTC)
    outcome, fh, th = _do_append(
        lore_root,
        files_touched=["/repo/x.py"],
        monkeypatch=monkeypatch,
    )
    result = write_or_update(
        outcome=outcome, scope=_make_scope(), transcript=_make_handle(),
        wiki_root=lore_root / "wiki" / "private",
        work_time=work_time, now=work_time,
        integration="claude-code",
        chunk_from_hash=fh, chunk_to_hash=th,
    )
    sidecar = outcome.buffer.read_sidecar()
    assert sidecar.stub_path == str(result.path)


def test_skipped_no_op_returns_none(lore_root, patch_collectors):
    """An empty-chunk AppendOutcome must short-circuit stub writing."""
    outcome = append_chunk(
        lore_root=lore_root, chunk_turns=[], local_date="2026-05-01",
        transcript_id="abc", integration="claude-code", wiki="private", scope="proj:x",
        cwd=lore_root, wiki_root=lore_root / "wiki" / "private", cfg=WikiConfig(),
    )
    result = write_or_update(
        outcome=outcome, scope=_make_scope(), transcript=_make_handle(),
        wiki_root=lore_root / "wiki" / "private",
        work_time=datetime(2026, 5, 1, 14, 32, tzinfo=UTC),
        now=datetime(2026, 5, 1, 14, 32, tzinfo=UTC),
        integration="claude-code",
    )
    assert result is None


def test_stub_state_filter_blocks_legacy_merge(
    lore_root, patch_collectors, monkeypatch,
):
    """A flag-false legacy chunk for a different transcript must NOT
    absorb a buffer-flush stub during staged rollout."""
    from lore_core.identity import session_note_dir
    from lore_core.session_writer import _find_todays_open_note

    work_time = datetime(2026, 5, 1, 14, 32, tzinfo=UTC)
    # Buffer-flush path writes a stub. transcript_id baked into the
    # buffer's sidecar drives the stub's frontmatter ``transcripts:``
    # list, so we drive the same id through ``_do_append`` and the
    # subsequent guard-query.
    outcome, fh, th = _do_append(
        lore_root,
        transcript_id="transcript-A",
        files_touched=["/repo/auth.py"],
        monkeypatch=monkeypatch,
    )
    result = write_or_update(
        outcome=outcome, scope=_make_scope(), transcript=_make_handle("transcript-A"),
        wiki_root=lore_root / "wiki" / "private",
        work_time=work_time, now=work_time,
        integration="claude-code",
        chunk_from_hash=fh, chunk_to_hash=th,
    )
    sessions_base = session_note_dir(lore_root / "wiki" / "private", "")

    # Legacy caller (different transcript_id) must NOT find this stub.
    other = _find_todays_open_note(
        sessions_base,
        scope=_make_scope(),
        work_date=work_time.date(),
        new_files_touched=["/repo/auth.py"],
        new_files_modified=["/repo/auth.py"],
        new_transcript_id="transcript-B",
    )
    assert other is None

    # Same transcript_id — would match if the stub_path lookup ever lost it.
    same = _find_todays_open_note(
        sessions_base,
        scope=_make_scope(),
        work_date=work_time.date(),
        new_files_touched=["/repo/auth.py"],
        new_files_modified=["/repo/auth.py"],
        new_transcript_id="transcript-A",
    )
    assert same == result.path


def test_stub_state_filter_blocks_legacy_when_transcript_id_unknown(
    lore_root, patch_collectors, monkeypatch,
):
    """An adapter-less caller (no transcript) cannot merge into a stub."""
    from lore_core.identity import session_note_dir
    from lore_core.session_writer import _find_todays_open_note

    work_time = datetime(2026, 5, 1, 14, 32, tzinfo=UTC)
    outcome, fh, th = _do_append(
        lore_root,
        files_touched=["/repo/auth.py"],
        monkeypatch=monkeypatch,
    )
    write_or_update(
        outcome=outcome, scope=_make_scope(), transcript=_make_handle(),
        wiki_root=lore_root / "wiki" / "private",
        work_time=work_time, now=work_time,
        integration="claude-code",
        chunk_from_hash=fh, chunk_to_hash=th,
    )
    sessions_base = session_note_dir(lore_root / "wiki" / "private", "")
    blocked = _find_todays_open_note(
        sessions_base,
        scope=_make_scope(),
        work_date=work_time.date(),
        new_files_touched=["/repo/auth.py"],
        new_files_modified=["/repo/auth.py"],
        new_transcript_id=None,
    )
    assert blocked is None
