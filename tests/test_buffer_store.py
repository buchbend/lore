"""Tests for the buffer-and-flush curator's storage primitives.

Covers Steps 1+2 of the very-good-thats-the-mossy-lobster plan:
schema round-trip, atomic-write semantics, state-machine CAS,
append + replay folding, concurrent-writer serialisation via flock,
close-to-_done relocation, and the cross-buffer iter helpers.

Reuses the threading/multiprocessing race patterns from
``tests/test_lockfile.py`` and ``tests/test_spawn_throttle_concurrent.py``.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import threading
from pathlib import Path

import pytest

from lore_curator.buffer_store import (
    Buffer,
    BufferTransitionError,
    Counters,
    FlushRequest,
    LastSeen,
    OwnerInfo,
    ReplayedBuffer,
    SCHEMA_VERSION,
    Sidecar,
    SlicePointer,
    buffers_dir,
    done_dir,
    iter_all,
    iter_for_pid,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


TID = "10413347-2001-48e6-b2d4-4b84bbd2eaf3"
DATE = "2026-05-01"


def _fresh(lore_root: Path, *, transcript_id: str = TID, local_date: str = DATE,
           part_index: int = 1, **patch) -> Buffer:
    """Open + init a buffer with a minimal but well-formed sidecar."""
    buf = Buffer.open(
        lore_root,
        transcript_id=transcript_id,
        local_date=local_date,
        part_index=part_index,
    )
    sidecar = Sidecar(
        transcript_id=transcript_id,
        local_date=local_date,
        integration="claude-code",
        wiki="private",
        scope="lore",
        cwd="/home/buchbend/git/lore",
        handle="buchbend",
        owner=OwnerInfo(pid=os.getpid(), start_ts=12345.0, host="testhost",
                        run_id="run-test", claude_session_id="cs-test"),
        part_index=part_index,
        **patch,
    )
    with buf.with_lock():
        buf.init_sidecar(sidecar)
    return buf


# ---------------------------------------------------------------------------
# Stem / path helpers
# ---------------------------------------------------------------------------


def test_buffers_dir_is_under_lore_dot_dir(tmp_path: Path) -> None:
    p = buffers_dir(tmp_path)
    assert p == tmp_path / ".lore" / "buffers"
    assert p.is_dir()


def test_done_dir_is_inside_buffers_dir(tmp_path: Path) -> None:
    p = done_dir(tmp_path)
    assert p == tmp_path / ".lore" / "buffers" / "_done"
    assert p.is_dir()


def test_open_rejects_invalid_local_date(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        Buffer.open(tmp_path, transcript_id=TID, local_date="not-a-date")


def test_open_rejects_invalid_transcript_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        Buffer.open(tmp_path, transcript_id="has__double_underscore", local_date=DATE)


def test_part_index_changes_stem(tmp_path: Path) -> None:
    a = Buffer.open(tmp_path, transcript_id=TID, local_date=DATE, part_index=1)
    b = Buffer.open(tmp_path, transcript_id=TID, local_date=DATE, part_index=2)
    c = Buffer.open(tmp_path, transcript_id=TID, local_date=DATE, part_index=3)
    assert a.stem != b.stem != c.stem
    assert b.stem.endswith("__part2")
    assert c.stem.endswith("__part3")
    # Part 1 has no suffix — so the buffer-flush change is invisible to
    # day-1 buffers and matches the "single Part" common case.
    assert "__part" not in a.stem


# ---------------------------------------------------------------------------
# Sidecar round-trip
# ---------------------------------------------------------------------------


def test_sidecar_round_trip_minimal() -> None:
    src = Sidecar(transcript_id=TID, local_date=DATE)
    raw = src.to_dict()
    back = Sidecar.from_dict(raw)
    assert back.transcript_id == TID
    assert back.local_date == DATE
    assert back.state == "accumulating"
    assert back.schema_version == SCHEMA_VERSION
    assert back.flush_requested is None  # default; should round-trip as None


def test_sidecar_round_trip_full() -> None:
    src = Sidecar(
        transcript_id=TID, local_date=DATE,
        integration="claude-code", wiki="private", scope="lore",
        cwd="/x", handle="user",
        owner=OwnerInfo(pid=42, start_ts=99.0, run_id="r", host="h", claude_session_id="c"),
        state="ready",
        created_at="2026-05-01T00:00:00Z",
        last_appended_at="2026-05-01T00:01:00Z",
        last_heartbeat="2026-05-01T00:02:00Z",
        counters=Counters(turn_count=10, prompt_chars=4096, files_touched_count=3),
        last_seen=LastSeen(content_hash="sha256:abc", index_hint=99),
        stub_path="/notes/stub.md",
        part_index=2,
        continuation_of=f"{TID}__20260501",
        flush_attempts=1,
        last_error="boom",
        flush_requested=FlushRequest(trigger="cap-trip", requested_at="t", by_pid=42),
    )
    back = Sidecar.from_dict(src.to_dict())
    assert back == src


def test_sidecar_dropping_optional_flush_request() -> None:
    """``flush_requested=None`` should NOT serialise as a null key."""
    src = Sidecar(transcript_id=TID, local_date=DATE)
    raw = src.to_dict()
    assert "flush_requested" not in raw


# ---------------------------------------------------------------------------
# init_sidecar / write atomicity
# ---------------------------------------------------------------------------


def test_init_sidecar_creates_file(tmp_path: Path) -> None:
    buf = _fresh(tmp_path)
    assert buf.sidecar_path.exists()
    sidecar = buf.read_sidecar()
    assert sidecar is not None
    assert sidecar.transcript_id == TID
    assert sidecar.created_at  # auto-stamped
    assert sidecar.last_heartbeat  # auto-stamped


def test_init_sidecar_refuses_to_overwrite(tmp_path: Path) -> None:
    buf = _fresh(tmp_path)
    with buf.with_lock():
        with pytest.raises(BufferTransitionError):
            buf.init_sidecar(Sidecar(transcript_id=TID, local_date=DATE))


def test_atomic_write_no_partial_file(tmp_path: Path) -> None:
    """A successful write must NOT leave a sibling .tmp file behind."""
    buf = _fresh(tmp_path)
    parent = buf.sidecar_path.parent
    siblings = [p.name for p in parent.iterdir() if p.name.endswith(".tmp")]
    assert siblings == []


def test_read_sidecar_returns_none_on_missing(tmp_path: Path) -> None:
    buf = Buffer.open(tmp_path, transcript_id=TID, local_date=DATE)
    assert buf.read_sidecar() is None


def test_read_sidecar_returns_none_on_corrupt(tmp_path: Path) -> None:
    buf = Buffer.open(tmp_path, transcript_id=TID, local_date=DATE)
    buf.sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    buf.sidecar_path.write_text("{ this is not json")
    assert buf.read_sidecar() is None


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("from_state,to_state,legal", [
    ("accumulating", "accumulating", True),
    ("accumulating", "ready", True),
    ("accumulating", "flushing", False),  # must go through ready
    ("accumulating", "closed", False),
    ("ready", "ready", True),
    ("ready", "flushing", True),
    ("ready", "accumulating", False),     # no rollback
    ("ready", "closed", False),
    ("flushing", "flushing", True),
    ("flushing", "closed", True),
    ("flushing", "ready", False),         # no rollback (Phase 2 retries inside flushing)
    ("flushing", "accumulating", False),
    ("closed", "closed", True),
    ("closed", "accumulating", False),
    ("closed", "ready", False),
    ("closed", "flushing", False),
])
def test_transition_legality(tmp_path: Path, from_state: str, to_state: str, legal: bool) -> None:
    buf = _fresh(tmp_path)
    # Walk to the start state through legal transitions.
    walk = {
        "accumulating": [],
        "ready": ["ready"],
        "flushing": ["ready", "flushing"],
        "closed": ["ready", "flushing", "closed"],
    }
    with buf.with_lock():
        for step in walk[from_state]:
            buf.transition(step)  # type: ignore[arg-type]
        if legal:
            buf.transition(to_state)  # type: ignore[arg-type]
            assert buf.read_sidecar().state == to_state  # type: ignore[union-attr]
        else:
            with pytest.raises(BufferTransitionError):
                buf.transition(to_state)  # type: ignore[arg-type]


def test_transition_patches_fields(tmp_path: Path) -> None:
    buf = _fresh(tmp_path)
    request = FlushRequest(trigger="session-end", requested_at="t", by_pid=42)
    with buf.with_lock():
        sidecar = buf.transition("ready", flush_requested=request)
    assert sidecar.state == "ready"
    assert sidecar.flush_requested == request
    assert buf.read_sidecar().flush_requested == request  # type: ignore[union-attr]


def test_transition_rejects_unknown_field(tmp_path: Path) -> None:
    buf = _fresh(tmp_path)
    with buf.with_lock():
        with pytest.raises(BufferTransitionError):
            buf.transition("ready", nonexistent_field=1)


def test_patch_does_not_change_state(tmp_path: Path) -> None:
    buf = _fresh(tmp_path)
    with buf.with_lock():
        buf.patch(flush_attempts=2, last_error="oops")
    s = buf.read_sidecar()
    assert s is not None
    assert s.state == "accumulating"
    assert s.flush_attempts == 2
    assert s.last_error == "oops"


def test_transition_without_sidecar_raises(tmp_path: Path) -> None:
    buf = Buffer.open(tmp_path, transcript_id=TID, local_date=DATE)
    with buf.with_lock():
        with pytest.raises(BufferTransitionError):
            buf.transition("ready")


# ---------------------------------------------------------------------------
# Append + replay
# ---------------------------------------------------------------------------


def test_append_and_replay_basic(tmp_path: Path) -> None:
    buf = _fresh(tmp_path)
    with buf.with_lock():
        buf.append_event({
            "type": "append",
            "files_touched": ["a.py", "b.py"],
            "commit_shas": ["abc1234"],
            "projects": ["proj-y"],
            "activity_commits": ["- abc1234 fix"],
            "turn_count_delta": 3,
            "prompt_chars_delta": 1024,
            "slice": {
                "from_hash": "sha256:from", "to_hash": "sha256:to",
                "from_index": 5, "to_index": 8,
            },
        })
        buf.append_event({
            "type": "append",
            "files_touched": ["a.py", "c.py"],  # a.py already seen
            "commit_shas": ["abc1234", "def5678"],  # abc already seen
            "turn_count_delta": 2,
            "prompt_chars_delta": 512,
        })

    rb = buf.replay()
    assert rb.files_touched == ["a.py", "b.py", "c.py"]  # first-seen order, no dups
    assert rb.commit_shas == ["abc1234", "def5678"]
    assert rb.projects == ["proj-y"]
    assert rb.activity_commits == ["- abc1234 fix"]
    assert rb.turn_count == 5
    assert rb.prompt_chars == 1024 + 512
    assert len(rb.slices) == 1
    assert rb.slices[0] == SlicePointer(
        from_hash="sha256:from", to_hash="sha256:to",
        from_index=5, to_index=8,
    )


def test_replay_skips_malformed_lines(tmp_path: Path) -> None:
    buf = _fresh(tmp_path)
    with buf.with_lock():
        buf.append_event({"type": "append", "files_touched": ["good.py"]})
    # Manually inject a broken line in between.
    with buf.log_path.open("a") as fh:
        fh.write("not-json\n")
        fh.write(json.dumps({"type": "append", "files_touched": ["after.py"]}) + "\n")
    rb = buf.replay()
    assert rb.files_touched == ["good.py", "after.py"]


def test_replay_unknown_event_type_ignored(tmp_path: Path) -> None:
    buf = _fresh(tmp_path)
    with buf.with_lock():
        buf.append_event({"type": "future-event", "weird_field": "x"})
        buf.append_event({"type": "append", "files_touched": ["real.py"]})
    rb = buf.replay()
    assert rb.files_touched == ["real.py"]


def test_replay_cap_tripped_recorded(tmp_path: Path) -> None:
    buf = _fresh(tmp_path)
    with buf.with_lock():
        buf.append_event({"type": "cap-tripped", "ts": "2026-05-01T12:00:00Z"})
    rb = buf.replay()
    assert rb.cap_tripped_at == "2026-05-01T12:00:00Z"


def test_append_auto_stamps_ts(tmp_path: Path) -> None:
    buf = _fresh(tmp_path)
    with buf.with_lock():
        buf.append_event({"type": "append", "files_touched": ["x"]})
    line = buf.log_path.read_text().strip()
    record = json.loads(line)
    assert "ts" in record
    assert record["ts"].endswith("Z")


def test_replay_empty_log(tmp_path: Path) -> None:
    buf = _fresh(tmp_path)
    rb = buf.replay()
    assert isinstance(rb, ReplayedBuffer)
    assert rb.files_touched == []
    assert rb.turn_count == 0


# ---------------------------------------------------------------------------
# Close → _done/
# ---------------------------------------------------------------------------


def test_close_relocates_both_files(tmp_path: Path) -> None:
    buf = _fresh(tmp_path)
    with buf.with_lock():
        buf.append_event({"type": "append", "files_touched": ["x.py"]})
    sidecar_name = buf.sidecar_path.name
    log_name = buf.log_path.name
    with buf.with_lock():
        result = buf.close()
    assert result is not None
    new_sidecar, new_log = result
    assert new_sidecar == done_dir(tmp_path) / sidecar_name
    assert new_log == done_dir(tmp_path) / log_name
    assert new_sidecar.exists()
    assert new_log.exists()
    assert not buf.sidecar_path.exists()
    assert not buf.log_path.exists()


def test_close_idempotent_when_already_gone(tmp_path: Path) -> None:
    buf = _fresh(tmp_path)
    with buf.with_lock():
        buf.close()
    with buf.with_lock():
        result = buf.close()
    assert result is None


def test_close_handles_missing_log(tmp_path: Path) -> None:
    """A buffer that never received an append (sidecar only) must close cleanly."""
    buf = _fresh(tmp_path)
    assert not buf.log_path.exists()
    with buf.with_lock():
        result = buf.close()
    assert result is not None
    new_sidecar, new_log = result
    assert new_sidecar.exists()
    assert not new_log.exists()


# ---------------------------------------------------------------------------
# Iteration helpers
# ---------------------------------------------------------------------------


def test_iter_all_yields_only_live_buffers(tmp_path: Path) -> None:
    a = _fresh(tmp_path, transcript_id="t-aaa", local_date="2026-05-01")
    b = _fresh(tmp_path, transcript_id="t-bbb", local_date="2026-05-01")
    c = _fresh(tmp_path, transcript_id="t-ccc", local_date="2026-05-02")
    # Close one — its files should land in _done/ and not be yielded.
    with c.with_lock():
        c.close()
    seen = sorted(buf.stem for buf in iter_all(tmp_path))
    expected = sorted([a.stem, b.stem])
    assert seen == expected


def test_iter_for_pid_filters_by_owner(tmp_path: Path) -> None:
    mine = os.getpid()
    other = mine + 99999
    a = _fresh(tmp_path, transcript_id="t-mine", local_date="2026-05-01")
    b = Buffer.open(tmp_path, transcript_id="t-other", local_date=DATE)
    with b.with_lock():
        b.init_sidecar(Sidecar(
            transcript_id="t-other", local_date=DATE,
            owner=OwnerInfo(pid=other, host="other-host"),
        ))
    seen_mine = [buf.stem for buf in iter_for_pid(tmp_path, mine)]
    seen_other = [buf.stem for buf in iter_for_pid(tmp_path, other)]
    assert seen_mine == [a.stem]
    assert seen_other == [b.stem]


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


def test_concurrent_appenders_serialise(tmp_path: Path) -> None:
    """Two threads appending simultaneously should produce N events, no torn lines."""
    buf = _fresh(tmp_path)
    n_per_thread = 50

    def writer(tag: str) -> None:
        for i in range(n_per_thread):
            with buf.with_lock():
                buf.append_event({
                    "type": "append",
                    "files_touched": [f"{tag}/{i}.py"],
                })

    threads = [threading.Thread(target=writer, args=(t,)) for t in ("A", "B", "C")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    rb = buf.replay()
    expected_count = n_per_thread * 3
    assert len(rb.files_touched) == expected_count
    # No torn lines — every line must be valid JSON.
    with buf.log_path.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                json.loads(line)  # raises if torn


def _proc_appender(lore_root_str: str, transcript_id: str, local_date: str,
                   tag: str, n: int) -> None:
    """Multiprocessing target: open + lock-and-append N events."""
    buf = Buffer.open(Path(lore_root_str),
                      transcript_id=transcript_id, local_date=local_date)
    for i in range(n):
        with buf.with_lock():
            buf.append_event({
                "type": "append",
                "files_touched": [f"{tag}/{i}.py"],
            })


def test_concurrent_appenders_across_processes(tmp_path: Path) -> None:
    """Cross-process flock guarantees the same atomicity as cross-thread."""
    _fresh(tmp_path)  # initialise
    n_per_proc = 25
    procs = [
        multiprocessing.Process(
            target=_proc_appender,
            args=(str(tmp_path), TID, DATE, tag, n_per_proc),
        )
        for tag in ("X", "Y", "Z")
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=20.0)
        assert p.exitcode == 0

    buf = Buffer.open(tmp_path, transcript_id=TID, local_date=DATE)
    rb = buf.replay()
    assert len(rb.files_touched) == n_per_proc * 3


def test_non_blocking_lock_yields_false_when_held(tmp_path: Path) -> None:
    buf = _fresh(tmp_path)
    blocked: list[bool] = []

    def hold() -> None:
        with buf.with_lock():
            event = threading.Event()
            event.wait(timeout=0.5)

    holder = threading.Thread(target=hold, daemon=True)
    holder.start()
    # Give the holder time to acquire.
    threading.Event().wait(timeout=0.05)
    with buf.with_lock(blocking=False) as held:
        blocked.append(held)
    holder.join(timeout=2.0)
    assert blocked == [False]


# ---------------------------------------------------------------------------
# from_sidecar_path round-trip (used by reaper + iter_all)
# ---------------------------------------------------------------------------


def test_from_sidecar_path_round_trip(tmp_path: Path) -> None:
    buf = _fresh(tmp_path)
    rebuilt = Buffer.from_sidecar_path(buf.sidecar_path)
    assert rebuilt.stem == buf.stem
    assert rebuilt.lore_root == tmp_path


def test_from_sidecar_path_rejects_wrong_layout(tmp_path: Path) -> None:
    weird = tmp_path / "weird.state.json"
    weird.write_text("{}")
    with pytest.raises(ValueError):
        Buffer.from_sidecar_path(weird)
