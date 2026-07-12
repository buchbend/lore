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


def test_cap_trip_bookkeeps_without_requesting_a_flush(lore_root, patch_collectors):
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
    # A cap-trip is bookkeeping, not a flush: it is recorded on the buffer's
    # log and nothing else happens. Writing here would mean composing the
    # session's first turns before its ending can say which of them mattered —
    # so the buffer keeps accumulating and the close path reads it whole.
    assert sidecar.state == "accumulating"
    assert sidecar.flush_requested is None
    log = (lore_root / ".lore" / "buffers" / f"{outcome.buffer.stem}.jsonl").read_text()
    assert '"type": "cap-tripped"' in log


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
    assert sidecar.state == "accumulating"
    assert sidecar.flush_requested is None


def test_cap_trip_keeps_single_buffer_for_session(lore_root, patch_collectors):
    """One session yields exactly one note: after a cap-trip the next
    heartbeat folds into the SAME buffer (stem), not a new part."""
    cfg = _make_cfg(cap_turns=4)
    o1 = append_chunk(
        lore_root=lore_root, chunk_turns=_make_turns(8), local_date="2026-05-01",
        transcript_id="abc", integration="claude-code", wiki="private", scope="proj:x",
        cwd=lore_root, wiki_root=lore_root / "wiki" / "private", cfg=cfg,
    )
    assert o1.cap_tripped is True
    o2 = append_chunk(
        lore_root=lore_root, chunk_turns=_make_turns(3), local_date="2026-05-01",
        transcript_id="abc", integration="claude-code", wiki="private", scope="proj:x",
        cwd=lore_root, wiki_root=lore_root / "wiki" / "private", cfg=cfg,
    )
    # Same buffer, still accumulating — the session is one buffer / one note.
    assert o2.buffer.stem == o1.buffer.stem
    assert "__part" not in o2.buffer.stem
    assert o2.buffer.read_sidecar().state == "accumulating"


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


def test_append_event_carries_files_modified_alongside_files_touched(
    lore_root, patch_collectors, monkeypatch,
):
    """step-1: every new append event emits ``files_modified`` (edits-only)
    next to the legacy ``files_touched`` (union). Replay folds both into
    distinct accumulators on the ReplayedBuffer."""
    import json

    monkeypatch.setattr(
        "lore_curator.buffer_append._files_touched_from_turns",
        lambda turns: ["/repo/a.py", "/repo/r.py"],  # union
    )
    monkeypatch.setattr(
        "lore_curator.buffer_append._files_modified_from_turns",
        lambda turns: ["/repo/a.py"],  # edits only
    )

    o1 = append_chunk(
        lore_root=lore_root, chunk_turns=_make_turns(2), local_date="2026-05-01",
        transcript_id="abc", integration="claude-code", wiki="private", scope="proj:x",
        cwd=lore_root, wiki_root=lore_root / "wiki" / "private", cfg=_make_cfg(),
    )

    log_text = (lore_root / ".lore" / "buffers" / f"{o1.buffer.stem}.jsonl").read_text()
    event = json.loads(log_text.splitlines()[0])
    assert event["files_touched"] == ["/repo/a.py", "/repo/r.py"]
    assert event["files_modified"] == ["/repo/a.py"]

    rb = o1.buffer.replay()
    assert rb.files_touched == ["/repo/a.py", "/repo/r.py"]
    assert rb.files_modified == ["/repo/a.py"]

    sidecar = o1.buffer.read_sidecar()
    assert sidecar.counters.files_touched_count == 2
    assert sidecar.counters.files_modified_count == 1

    assert o1.files_modified == ["/repo/a.py"]
    assert o1.new_files_modified == ["/repo/a.py"]


def test_legacy_event_without_files_modified_replays_clean(lore_root, patch_collectors):
    """A v1-shaped event log (no ``files_modified`` key) must still fold
    without exception; ``rb.files_modified`` stays empty for that buffer
    rather than mis-classifying reads as edits."""
    import json

    # Hand-craft a v1-shaped sidecar + event log under .lore/buffers/_done/
    # to simulate an archived buffer the new code is asked to read.
    buffers = lore_root / ".lore" / "buffers"
    buffers.mkdir(parents=True, exist_ok=True)
    stem = "abc__20260501"
    sidecar_path = buffers / f"{stem}.state.json"
    log_path = buffers / f"{stem}.jsonl"

    sidecar_payload = {
        "schema_version": 1,
        "transcript_id": "abc",
        "local_date": "2026-05-01",
        "integration": "claude-code",
        "wiki": "private",
        "scope": "proj:x",
        "state": "closed",
        "counters": {
            "turn_count": 2,
            "prompt_chars": 10,
            "files_touched_count": 2,
            # files_modified_count absent — defaults to 0
        },
    }
    sidecar_path.write_text(json.dumps(sidecar_payload))
    legacy_event = {
        "type": "append",
        "files_touched": ["/r/a.py", "/r/b.py"],
        # files_modified absent (v1 shape)
        "turn_count_delta": 2,
        "prompt_chars_delta": 10,
    }
    log_path.write_text(json.dumps(legacy_event) + "\n")

    buf = Buffer(lore_root, stem)
    rb = buf.replay()
    assert rb.files_touched == ["/r/a.py", "/r/b.py"]
    assert rb.files_modified == []  # v1 default; no mis-classification

    sidecar = buf.read_sidecar()
    assert sidecar.counters.files_modified_count == 0  # v1 default


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


