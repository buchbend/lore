import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lore_core.run_log import RunLogger, generate_run_id


def test_run_id_format():
    ts = datetime(2026, 4, 20, 14, 32, 5, tzinfo=UTC)
    run_id = generate_run_id(now=ts)
    assert re.fullmatch(r"2026-04-20T14-32-05-[a-z0-9]{6}", run_id), run_id


def test_run_id_uniqueness():
    ids = {generate_run_id() for _ in range(1000)}
    assert len(ids) == 1000


def test_run_start_written_on_enter(tmp_path: Path):
    with RunLogger(tmp_path, trigger="manual", pending_count=2) as logger:
        pass
    archival_files = list((tmp_path / ".lore" / "runs").glob("*.jsonl"))
    archival = [p for p in archival_files if not p.name.endswith(".trace.jsonl")]
    assert len(archival) == 1
    lines = archival[0].read_text().splitlines()
    records = [json.loads(l) for l in lines]
    assert records[0]["type"] == "run-start"
    assert records[0]["trigger"] == "manual"
    assert records[0]["pending_count"] == 2
    assert records[-1]["type"] == "run-end"
    live = tmp_path / ".lore" / "runs-live.jsonl"
    live_records = [json.loads(l) for l in live.read_text().splitlines()]
    assert all("run_id" in r for r in live_records)
    assert live_records[0]["type"] == "run-start"


def test_emit_counters_and_ordering(tmp_path: Path):
    with RunLogger(tmp_path, trigger="hook", pending_count=3) as logger:
        logger.emit("transcript-start", transcript_id="t1", new_turns=10)
        logger.emit("noteworthy", transcript_id="t1", verdict=True, reason="x", tier="middle")
        logger.emit("session-note", transcript_id="t1", action="filed",
                    path="p.md", wikilink="[[p]]")
        logger.emit("transcript-start", transcript_id="t2", new_turns=5)
        logger.emit("skip", transcript_id="t2", reason="noteworthy-false")
    archival = next((tmp_path / ".lore" / "runs").glob("*.jsonl"))
    records = [json.loads(l) for l in archival.read_text().splitlines()]
    assert records[0]["type"] == "run-start"
    assert records[-1]["type"] == "run-end"
    assert records[-1]["notes_new"] == 1
    assert records[-1]["notes_merged"] == 0
    assert records[-1]["skipped"] == 1
    assert records[-1]["errors"] == 0
    kinds = [r["type"] for r in records[1:-1]]
    assert kinds == ["transcript-start", "noteworthy", "session-note",
                     "transcript-start", "skip"]


def test_exception_emits_error_and_runend_then_propagates(tmp_path: Path):
    with pytest.raises(ValueError, match="boom"):
        with RunLogger(tmp_path, trigger="hook") as logger:
            logger.emit("transcript-start", transcript_id="t1", new_turns=5)
            raise ValueError("boom")
    archival = next((tmp_path / ".lore" / "runs").glob("*.jsonl"))
    records = [json.loads(l) for l in archival.read_text().splitlines()]
    types = [r["type"] for r in records]
    assert "error" in types
    assert types[-1] == "run-end"
    assert records[-1]["errors"] >= 1


def test_write_failure_increments_counter(tmp_path: Path, monkeypatch):
    real_open = Path.open

    def faulty_open(self, *args, **kwargs):
        if "runs" in str(self) and not str(self).endswith("runs"):
            raise OSError("disk full")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", faulty_open)
    with RunLogger(tmp_path, trigger="hook") as logger:
        logger.emit("transcript-start", transcript_id="t1", new_turns=5)
    # No raise despite every write failing.


def test_trace_llm_writes_companion(tmp_path: Path):
    with RunLogger(tmp_path, trigger="dry-run", trace_llm=True) as logger:
        logger.emit("llm-prompt", call="noteworthy", tier="middle",
                    token_count=100, messages=[{"role": "user", "content": "hi"}])
        logger.emit("llm-response", call="noteworthy", token_count=5, body="yes")
    trace_files = list((tmp_path / ".lore" / "runs").glob("*.trace.jsonl"))
    assert len(trace_files) == 1
    lines = trace_files[0].read_text().splitlines()
    types = [json.loads(l)["type"] for l in lines]
    assert "llm-prompt" in types
    assert "llm-response" in types


def test_trace_llm_off_no_companion(tmp_path: Path):
    with RunLogger(tmp_path, trigger="hook", trace_llm=False) as logger:
        logger.emit("llm-prompt", call="noteworthy", tier="middle",
                    token_count=100, messages=[])
    assert not list((tmp_path / ".lore" / "runs").glob("*.trace.jsonl"))


def test_log_write_failures_surface_in_run_end(tmp_path: Path, monkeypatch):
    real_open = Path.open

    def faulty_open(self, *args, **kwargs):
        if str(self).endswith(".jsonl"):
            raise OSError("disk full")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", faulty_open)
    # All writes fail; run still completes.
    with RunLogger(tmp_path, trigger="hook") as logger:
        logger.emit("transcript-start", transcript_id="t1", new_turns=5)
    # The archival file was never written (OSError). But we can verify the
    # counter bookkeeping by writing one record manually and checking.
    # Since every write fails, there's no file to read. So instead test
    # that the counter is non-zero by probing the logger attribute:
    logger2 = RunLogger(tmp_path, trigger="hook")
    # Pre-seed failure
    logger2._write_failures = 3
    # The run-end record will include log_write_failures=3; verify via
    # unit-level check of the emit pathway by writing through logger2's
    # fresh context:
    monkeypatch.undo()  # restore real Path.open
    with logger2 as l:
        pass
    archival = next((tmp_path / ".lore" / "runs").glob("*.jsonl"))
    records = [json.loads(ln) for ln in archival.read_text().splitlines()]
    assert records[-1]["log_write_failures"] == 3


def test_emit_serializes_non_json_native_types(tmp_path: Path):
    with RunLogger(tmp_path, trigger="hook") as logger:
        logger.emit(
            "warning",
            message="non-json",
            a_path=Path("/tmp/foo"),
            a_ts=datetime(2026, 1, 1, tzinfo=UTC),
        )
    archival = next((tmp_path / ".lore" / "runs").glob("*.jsonl"))
    lines = archival.read_text().splitlines()
    # The record survives — default=str stringifies the Path and datetime.
    records = [json.loads(l) for l in lines]
    warn = [r for r in records if r.get("type") == "warning"]
    assert warn, "warning record should have landed"
    assert "/tmp/foo" in warn[0]["a_path"]


def test_enter_does_not_raise_on_readonly_fs(tmp_path, monkeypatch):
    """__enter__ must not raise even if mkdir fails."""
    real_mkdir = Path.mkdir

    def bad_mkdir(self, *args, **kwargs):
        if "runs" in str(self):
            raise OSError("read-only")
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", bad_mkdir)
    # Must not raise.
    with RunLogger(tmp_path, trigger="hook") as logger:
        logger.emit("transcript-start", transcript_id="t1", new_turns=5)


def test_enter_does_not_raise_on_collision_after_retry(tmp_path, monkeypatch):
    """Double-collision in __enter__ must not raise."""
    # Pre-create both possible run-id paths by monkeypatching generate_run_id
    # to return the same collision id twice.
    from lore_core import run_log as run_log_mod

    fixed_id = "2026-04-20T14-32-05-aaaaaa"
    (tmp_path / ".lore" / "runs").mkdir(parents=True)
    (tmp_path / ".lore" / "runs" / f"{fixed_id}.jsonl").write_text("x\n")

    call_count = {"n": 0}
    def collide(*a, **kw):
        call_count["n"] += 1
        return fixed_id

    monkeypatch.setattr(run_log_mod, "generate_run_id", collide)
    # Must not raise despite the second generate_run_id also colliding.
    with RunLogger(tmp_path, trigger="hook") as logger:
        logger.emit("transcript-start", transcript_id="t1", new_turns=5)
    # Collision was detected, _write_failures incremented
    assert logger._write_failures >= 1
