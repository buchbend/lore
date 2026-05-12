"""Issue #52: ``synth_in_place`` keeps the buffer alive across
session-end / pre-compact boundaries so one transcript-day stays one
note. ``synth_and_close`` retains the original close-and-archive
semantics for cap-trip / reaper.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from lore_core.schema import parse_frontmatter
from lore_core.types import Scope, TranscriptHandle, Turn
from lore_core.wiki_config import WikiConfig
from lore_curator.buffer_append import append_chunk
from lore_curator.buffer_store import done_dir
from lore_curator.stub_note import write_or_update
from lore_curator.synthesis import synth_and_close, synth_in_place


@pytest.fixture
def lore_root(tmp_path: Path) -> Path:
    (tmp_path / ".lore" / "buffers").mkdir(parents=True)
    (tmp_path / "wiki" / "private").mkdir(parents=True)
    return tmp_path


@pytest.fixture(autouse=True)
def patch_collectors(monkeypatch):
    monkeypatch.setattr("lore_curator.session_activity.collect_commits_by_sha", lambda *a, **kw: [])
    monkeypatch.setattr("lore_curator.session_activity.collect_issues_in_window", lambda *a, **kw: ([], []))
    monkeypatch.setattr("lore_curator.session_activity.collect_projects_for_session", lambda **kw: [])
    monkeypatch.setattr("lore_core.git.git_repo_root", lambda cwd: None)
    monkeypatch.setattr("lore_core.git.current_repo", lambda cwd: "")


def _scope() -> Scope:
    return Scope(wiki="private", scope="proj:feature", backend="none",
                 claude_md_path=Path("/tmp/CLAUDE.md"))


def _handle(transcript_id: str = "abc") -> TranscriptHandle:
    return TranscriptHandle(
        integration="claude-code",
        id=transcript_id,
        path=Path("/tmp/t.jsonl"),
        cwd=Path("/tmp"),
        mtime=datetime.now(UTC),
    )


def _seed_buffer_with_stub(lore_root, monkeypatch, *, files_modified=None,
                          transcript_id="abc"):
    if files_modified is not None:
        monkeypatch.setattr(
            "lore_curator.buffer_append._files_modified_from_turns",
            lambda _turns: list(files_modified),
        )
        monkeypatch.setattr(
            "lore_curator.buffer_append._files_touched_from_turns",
            lambda _turns: list(files_modified),
        )
    turns = [Turn(index=0, timestamp=None, role="user", text="hello")]
    work_time = datetime(2026, 5, 1, 14, 32, tzinfo=UTC)
    outcome = append_chunk(
        lore_root=lore_root, chunk_turns=turns, local_date="2026-05-01",
        transcript_id=transcript_id, integration="claude-code", wiki="private",
        scope="proj:feature", cwd=lore_root,
        wiki_root=lore_root / "wiki" / "private", cfg=WikiConfig(),
    )
    write_or_update(
        outcome=outcome, scope=_scope(), transcript=_handle(transcript_id),
        wiki_root=lore_root / "wiki" / "private",
        work_time=work_time, now=work_time,
        integration="claude-code",
        chunk_from_hash=turns[0].content_hash(),
        chunk_to_hash=turns[-1].content_hash(),
    )
    return outcome.buffer, work_time


def test_synth_in_place_leaves_buffer_accumulating(lore_root, monkeypatch):
    buf, _ = _seed_buffer_with_stub(
        lore_root, monkeypatch, files_modified=["/repo/auth.py"],
    )
    out = synth_in_place(
        buf.sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        # No LLM client → Phase 2 skipped, Phase 1 only.
    )
    assert out.phase1_completed is True
    sidecar = buf.read_sidecar()
    # Buffer is still alive — next chunk on the same transcript+date
    # continues into the same stub instead of opening a duplicate Part-1.
    assert sidecar.state == "accumulating"
    # Sidecar lives, NOT in _done/.
    assert buf.sidecar_path.exists()
    assert not (done_dir(lore_root) / buf.sidecar_path.name).exists()


def test_synth_in_place_retains_state_stub_marker(lore_root, monkeypatch):
    """The merge gate's stub-protection branch keys on
    ``state: stub``; in_place must NOT pop it."""
    buf, _ = _seed_buffer_with_stub(
        lore_root, monkeypatch, files_modified=["/repo/auth.py"],
    )
    out = synth_in_place(
        buf.sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
    )
    assert out.stub_path is not None
    fm = parse_frontmatter(out.stub_path.read_text())
    assert fm.get("state") == "stub"


def test_synth_and_close_pops_state_stub_and_archives(lore_root, monkeypatch):
    buf, _ = _seed_buffer_with_stub(
        lore_root, monkeypatch, files_modified=["/repo/auth.py"],
    )
    out = synth_and_close(
        buf.sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
    )
    assert out.phase1_completed is True
    fm = parse_frontmatter(out.stub_path.read_text())
    assert "state" not in fm  # stub marker dropped
    # Sidecar archived.
    assert not buf.sidecar_path.exists()
    assert (done_dir(lore_root) / buf.sidecar_path.name).exists()


def test_synth_in_place_clears_flush_request(lore_root, monkeypatch):
    """One-shot semantic: the marker that triggered synth must clear
    so the next heartbeat doesn't loop on the same buffer."""
    from lore_curator.buffer_store import FlushRequest

    buf, _ = _seed_buffer_with_stub(
        lore_root, monkeypatch, files_modified=["/repo/auth.py"],
    )
    with buf.with_lock():
        buf.patch(flush_requested=FlushRequest(
            trigger="session-end", requested_at="2026-05-07T10:00Z",
            by_pid=42, mode="in_place",
        ))
    synth_in_place(
        buf.sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
    )
    sidecar = buf.read_sidecar()
    assert sidecar.flush_requested is None


def test_files_modified_and_files_read_in_frontmatter(lore_root, monkeypatch):
    """End-to-end: a buffer with both edits and reads produces honest
    file-list frontmatter — ``files_modified`` for edits, ``files_read``
    for the strict-superset reads, no ``files_touched``."""
    monkeypatch.setattr(
        "lore_curator.buffer_append._files_modified_from_turns",
        lambda _turns: ["/repo/auth.py"],
    )
    monkeypatch.setattr(
        "lore_curator.buffer_append._files_read_from_turns",
        lambda _turns: ["/repo/auth.py", "/repo/README.md"],
    )
    monkeypatch.setattr(
        "lore_curator.buffer_append._files_touched_from_turns",
        lambda _turns: ["/repo/auth.py", "/repo/README.md"],
    )
    turns = [Turn(index=0, timestamp=None, role="user", text="hello")]
    work_time = datetime(2026, 5, 1, 14, 32, tzinfo=UTC)
    outcome = append_chunk(
        lore_root=lore_root, chunk_turns=turns, local_date="2026-05-01",
        transcript_id="abc", integration="claude-code", wiki="private",
        scope="proj:feature", cwd=lore_root,
        wiki_root=lore_root / "wiki" / "private", cfg=WikiConfig(),
    )
    result = write_or_update(
        outcome=outcome, scope=_scope(), transcript=_handle(),
        wiki_root=lore_root / "wiki" / "private",
        work_time=work_time, now=work_time,
        integration="claude-code",
        chunk_from_hash=turns[0].content_hash(),
        chunk_to_hash=turns[-1].content_hash(),
    )
    fm = parse_frontmatter(result.path.read_text())
    assert fm["files_modified"] == ["/repo/auth.py"]
    assert fm["files_read"] == ["/repo/README.md"]
    assert "files_touched" not in fm

    out = synth_and_close(
        outcome.buffer.sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
    )
    fm_final = parse_frontmatter(out.stub_path.read_text())
    assert fm_final["files_modified"] == ["/repo/auth.py"]
    assert fm_final["files_read"] == ["/repo/README.md"]
    assert "files_touched" not in fm_final


def test_one_note_per_transcript_day_across_in_place_calls(lore_root, monkeypatch):
    """The headline regression test: pre-compact + session-end across
    one continuous transcript must produce ONE note, not three. The
    same buffer absorbs subsequent chunks because state stays
    ``accumulating``."""
    buf, work_time = _seed_buffer_with_stub(
        lore_root, monkeypatch, files_modified=["/repo/a.py"],
    )

    # Simulated pre-compact firing.
    out_pc = synth_in_place(
        buf.sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
    )
    assert out_pc.phase1_completed
    note_path = out_pc.stub_path

    # Conversation continues — second heartbeat with new file.
    monkeypatch.setattr(
        "lore_curator.buffer_append._files_modified_from_turns",
        lambda _turns: ["/repo/b.py"],
    )
    monkeypatch.setattr(
        "lore_curator.buffer_append._files_touched_from_turns",
        lambda _turns: ["/repo/b.py"],
    )
    turns_b = [Turn(index=2, timestamp=None, role="user", text="more")]
    outcome2 = append_chunk(
        lore_root=lore_root, chunk_turns=turns_b, local_date="2026-05-01",
        transcript_id="abc", integration="claude-code", wiki="private",
        scope="proj:feature", cwd=lore_root,
        wiki_root=lore_root / "wiki" / "private", cfg=WikiConfig(),
    )
    # Same buffer! No Part-2 split.
    assert outcome2.buffer.stem == buf.stem
    write_or_update(
        outcome=outcome2, scope=_scope(), transcript=_handle(),
        wiki_root=lore_root / "wiki" / "private",
        work_time=work_time, now=work_time,
        integration="claude-code",
        chunk_from_hash=turns_b[0].content_hash(),
        chunk_to_hash=turns_b[-1].content_hash(),
    )

    # Session-end fires another in_place synth.
    out_se = synth_in_place(
        buf.sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
    )
    assert out_se.phase1_completed
    # SAME note path — no fragmentation.
    assert out_se.stub_path == note_path

    fm = parse_frontmatter(note_path.read_text())
    # Both files accumulated.
    assert sorted(fm["files_modified"]) == ["/repo/a.py", "/repo/b.py"]
    # Still marked ``state: stub`` because the buffer is still live.
    assert fm.get("state") == "stub"


