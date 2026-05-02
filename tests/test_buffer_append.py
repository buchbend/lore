"""Tests for lore_curator.buffer_append — heartbeat path."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from lore_core.types import Scope, TranscriptHandle, Turn
from lore_core.wiki_config import WikiConfig
from lore_curator.buffer_append import AppendOutcome, append_chunk
from lore_curator.buffer_store import Buffer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cfg(*, cap_turns: int = 120, cap_chars: int = 240_000) -> WikiConfig:
    cfg = WikiConfig()
    cfg.curator.synthesis_buffer_cap_turns = cap_turns
    cfg.curator.synthesis_buffer_cap_chars = cap_chars
    return cfg


def _make_handle() -> TranscriptHandle:
    return TranscriptHandle(
        integration="claude-code",
        id="transcript-abc",
        path=Path("/tmp/transcript.jsonl"),
        cwd=Path("/tmp"),
        mtime=datetime.now(UTC),
    )


def _make_turns(n: int = 2, *, prompt_chars_each: int = 10) -> list[Turn]:
    """n turns alternating user/assistant, each carrying ``prompt_chars_each``
    characters of text.
    """
    turns: list[Turn] = []
    text = "x" * prompt_chars_each
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        turns.append(Turn(index=i, timestamp=None, role=role, text=text))
    return turns


@pytest.fixture
def lore_root(tmp_path: Path) -> Path:
    (tmp_path / ".lore" / "buffers").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def patch_collectors(monkeypatch):
    """Stub the activity collectors so the heartbeat path is hermetic.

    ``_collect_activity`` reaches into git + gh; tests don't want that.
    Patch the ``session_activity`` namespace where ``_collect_activity``
    looks up its callees.
    """
    monkeypatch.setattr(
        "lore_curator.session_activity.collect_commits_by_sha",
        lambda *a, **kw: [],
    )
    monkeypatch.setattr(
        "lore_curator.session_activity.collect_issues_in_window",
        lambda *a, **kw: ([], []),
    )
    monkeypatch.setattr(
        "lore_curator.session_activity.collect_plans_advanced",
        lambda **kw: [],
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_first_heartbeat_initialises_buffer_state(lore_root, patch_collectors):
    turns = _make_turns(2)
    outcome = append_chunk(
        lore_root=lore_root,
        chunk_turns=turns,
        local_date="2026-05-01",
        transcript_id="abc",
        integration="claude-code",
        wiki="private",
        scope="proj:feature",
        cwd=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        cfg=_make_cfg(),
    )
    assert outcome.is_new_buffer is True
    assert outcome.skipped_no_op is False
    sidecar = outcome.buffer.read_sidecar()
    assert sidecar is not None
    assert sidecar.state == "accumulating"
    assert sidecar.transcript_id == "abc"
    assert sidecar.integration == "claude-code"
    assert sidecar.wiki == "private"
    assert sidecar.scope == "proj:feature"
    assert sidecar.counters.turn_count == 2
    assert sidecar.counters.prompt_chars > 0
    assert sidecar.last_seen.content_hash == turns[-1].content_hash()
    assert sidecar.last_seen.index_hint == turns[-1].index


def test_subsequent_heartbeat_advances_counters(lore_root, patch_collectors):
    turns_a = _make_turns(2)
    turns_b = [
        Turn(index=2, timestamp=None, role="user", text="more"),
        Turn(index=3, timestamp=None, role="assistant", text="again"),
    ]

    cfg = _make_cfg()
    o1 = append_chunk(
        lore_root=lore_root,
        chunk_turns=turns_a,
        local_date="2026-05-01",
        transcript_id="abc",
        integration="claude-code",
        wiki="private",
        scope="proj:x",
        cwd=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        cfg=cfg,
    )
    o2 = append_chunk(
        lore_root=lore_root,
        chunk_turns=turns_b,
        local_date="2026-05-01",
        transcript_id="abc",
        integration="claude-code",
        wiki="private",
        scope="proj:x",
        cwd=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        cfg=cfg,
    )
    assert o1.is_new_buffer is True
    assert o2.is_new_buffer is False
    sidecar = o2.buffer.read_sidecar()
    assert sidecar.counters.turn_count == 4
    assert sidecar.last_seen.index_hint == 3
    # JSONL has 2 append events
    log = (lore_root / ".lore" / "buffers" / f"{o2.buffer.stem}.jsonl").read_text()
    assert log.count('"type": "append"') == 2


def test_cap_trip_flips_buffer_to_ready(lore_root, patch_collectors):
    turns = _make_turns(8)
    cfg = _make_cfg(cap_turns=4)  # tripped on first heartbeat

    outcome = append_chunk(
        lore_root=lore_root,
        chunk_turns=turns,
        local_date="2026-05-01",
        transcript_id="abc",
        integration="claude-code",
        wiki="private",
        scope="proj:x",
        cwd=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        cfg=cfg,
    )
    assert outcome.cap_tripped is True
    sidecar = outcome.buffer.read_sidecar()
    assert sidecar.state == "ready"
    assert sidecar.flush_requested is not None
    assert sidecar.flush_requested.trigger == "cap-trip"


def test_cap_trip_chars_threshold(lore_root, patch_collectors):
    turns = _make_turns(2, prompt_chars_each=200)
    cfg = _make_cfg(cap_turns=10_000, cap_chars=300)

    outcome = append_chunk(
        lore_root=lore_root,
        chunk_turns=turns,
        local_date="2026-05-01",
        transcript_id="abc",
        integration="claude-code",
        wiki="private",
        scope="proj:x",
        cwd=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        cfg=cfg,
    )
    assert outcome.cap_tripped is True
    sidecar = outcome.buffer.read_sidecar()
    assert sidecar.state == "ready"


def test_no_op_chunk_does_not_create_buffer(lore_root, patch_collectors):
    outcome = append_chunk(
        lore_root=lore_root,
        chunk_turns=[],
        local_date="2026-05-01",
        transcript_id="abc",
        integration="claude-code",
        wiki="private",
        scope="proj:x",
        cwd=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        cfg=_make_cfg(),
    )
    assert outcome.skipped_no_op is True
    assert outcome.buffer.read_sidecar() is None


def test_idempotent_replay_dedups_files_touched(lore_root, patch_collectors, monkeypatch):
    # Make _files_touched_from_turns return a stable set so replay folds
    # across appends.
    monkeypatch.setattr(
        "lore_curator.buffer_append._files_touched_from_turns",
        lambda turns: ["/repo/a.py", "/repo/b.py"],
    )
    turns_a = _make_turns(2)
    turns_b = _make_turns(2)  # same content; same hashes; same files

    cfg = _make_cfg()
    o1 = append_chunk(
        lore_root=lore_root,
        chunk_turns=turns_a,
        local_date="2026-05-01",
        transcript_id="abc",
        integration="claude-code",
        wiki="private",
        scope="proj:x",
        cwd=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        cfg=cfg,
    )
    o2 = append_chunk(
        lore_root=lore_root,
        chunk_turns=turns_b,
        local_date="2026-05-01",
        transcript_id="abc",
        integration="claude-code",
        wiki="private",
        scope="proj:x",
        cwd=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        cfg=cfg,
    )

    rb = o2.buffer.replay()
    assert rb.files_touched == ["/repo/a.py", "/repo/b.py"]
    # Second heartbeat reports no new files.
    assert o2.new_files_touched == []
    # Counters still grew (turn_count is summed).
    assert rb.turn_count == 4


def test_accumulators_unchanged_when_chunk_repeats(lore_root, patch_collectors, monkeypatch):
    monkeypatch.setattr(
        "lore_curator.buffer_append._files_touched_from_turns",
        lambda turns: ["/repo/a.py"],
    )
    turns_a = _make_turns(2)
    turns_b = _make_turns(2)

    cfg = _make_cfg()
    append_chunk(
        lore_root=lore_root, chunk_turns=turns_a, local_date="2026-05-01",
        transcript_id="abc", integration="claude-code", wiki="private", scope="proj:x",
        cwd=lore_root, wiki_root=lore_root / "wiki" / "private", cfg=cfg,
    )
    o2 = append_chunk(
        lore_root=lore_root, chunk_turns=turns_b, local_date="2026-05-01",
        transcript_id="abc", integration="claude-code", wiki="private", scope="proj:x",
        cwd=lore_root, wiki_root=lore_root / "wiki" / "private", cfg=cfg,
    )
    assert o2.accumulators_unchanged is True


def test_state_flushing_or_closed_skips_append(lore_root, patch_collectors):
    # Initialise a buffer.
    o1 = append_chunk(
        lore_root=lore_root, chunk_turns=_make_turns(2), local_date="2026-05-01",
        transcript_id="abc", integration="claude-code", wiki="private", scope="proj:x",
        cwd=lore_root, wiki_root=lore_root / "wiki" / "private", cfg=_make_cfg(),
    )

    # Force-flip to flushing (simulate a flush worker taking ownership).
    with o1.buffer.with_lock():
        o1.buffer.transition("ready")
        o1.buffer.transition("flushing")

    # A subsequent heartbeat must NOT mutate the buffer.
    log_before = (lore_root / ".lore" / "buffers" / f"{o1.buffer.stem}.jsonl").read_text()
    o2 = append_chunk(
        lore_root=lore_root, chunk_turns=_make_turns(2), local_date="2026-05-01",
        transcript_id="abc", integration="claude-code", wiki="private", scope="proj:x",
        cwd=lore_root, wiki_root=lore_root / "wiki" / "private", cfg=_make_cfg(),
    )
    log_after = (lore_root / ".lore" / "buffers" / f"{o1.buffer.stem}.jsonl").read_text()
    assert log_before == log_after
    assert o2.skipped_no_op is True
    assert o2.sidecar_after.state == "flushing"


def test_owner_info_stamped(lore_root, patch_collectors):
    outcome = append_chunk(
        lore_root=lore_root, chunk_turns=_make_turns(2), local_date="2026-05-01",
        transcript_id="abc", integration="claude-code", wiki="private", scope="proj:x",
        cwd=lore_root, wiki_root=lore_root / "wiki" / "private", cfg=_make_cfg(),
        owner_run_id="run-XYZ",
        owner_claude_session_id="session-123",
    )
    sidecar = outcome.buffer.read_sidecar()
    import os as _os
    assert sidecar.owner.pid == _os.getpid()
    assert sidecar.owner.run_id == "run-XYZ"
    assert sidecar.owner.claude_session_id == "session-123"
    assert sidecar.owner.host  # non-empty


def test_emits_telemetry(lore_root, patch_collectors, monkeypatch):
    events: list[tuple[str, dict]] = []

    class _RecLogger:
        run_id = "test-run"

        def emit(self, name, **kw):
            events.append((name, kw))

    append_chunk(
        lore_root=lore_root, chunk_turns=_make_turns(2), local_date="2026-05-01",
        transcript_id="abc", integration="claude-code", wiki="private", scope="proj:x",
        cwd=lore_root, wiki_root=lore_root / "wiki" / "private", cfg=_make_cfg(),
        logger=_RecLogger(),
    )
    names = [n for (n, _) in events]
    assert "buffer-opened" in names
    assert "buffer-appended" in names


def test_part_index_2_uses_separate_stem(lore_root, patch_collectors):
    """Cap-trip's Part-N split (Step 8) opens a separate buffer; verify the
    buffer addressing primitive accepts ``part_index=2`` cleanly."""
    o1 = append_chunk(
        lore_root=lore_root, chunk_turns=_make_turns(2), local_date="2026-05-01",
        transcript_id="abc", integration="claude-code", wiki="private", scope="proj:x",
        cwd=lore_root, wiki_root=lore_root / "wiki" / "private", cfg=_make_cfg(),
        part_index=1,
    )
    o2 = append_chunk(
        lore_root=lore_root, chunk_turns=_make_turns(2), local_date="2026-05-01",
        transcript_id="abc", integration="claude-code", wiki="private", scope="proj:x",
        cwd=lore_root, wiki_root=lore_root / "wiki" / "private", cfg=_make_cfg(),
        part_index=2,
    )
    assert o1.buffer.stem != o2.buffer.stem
    assert o2.buffer.stem.endswith("__part2")
