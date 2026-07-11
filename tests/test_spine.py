"""Event-spine foundation tests (issue #185).

Covers the envelope schema, the closed error-code enum, the O_APPEND +
flock writer, degrade-marker on write failure, and the hook producer
adapter. These are the regression net the later telemetry slices
(#188 trace_id, #189 flush state machine, #190 janitor) build on.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from lore_core.spine import (
    ENVELOPE_FIELDS,
    LEVELS,
    SCHEMA_VERSION,
    SOURCES,
    ErrorCode,
    SpineWriter,
    emit_hook_event,
    read_spine,
    validate_envelope,
)


def _read(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# AC1 — envelope schema + closed error-code enum, in one module
# ---------------------------------------------------------------------------


def test_emit_produces_complete_envelope(tmp_path: Path) -> None:
    SpineWriter(tmp_path).emit(source="hook", event="session-end")
    rec = _read(tmp_path / ".lore" / "spine.jsonl")[0]
    # Every envelope field present — never absent, even when unknown.
    assert set(rec.keys()) == set(ENVELOPE_FIELDS)
    for unknown in ("trace_id", "session_id", "run_id", "wiki", "scope", "error_code"):
        assert rec[unknown] is None
    assert rec["v"] == SCHEMA_VERSION
    assert rec["source"] == "hook"
    assert rec["level"] == "info"
    assert rec["data"] == {}
    validate_envelope(rec)


def test_every_source_and_level_validates(tmp_path: Path) -> None:
    for src in SOURCES:
        for lvl in LEVELS:
            SpineWriter(tmp_path).emit(source=src, event="e", level=lvl)
    for rec in _read(tmp_path / ".lore" / "spine.jsonl"):
        validate_envelope(rec)  # must not raise


def test_error_code_from_closed_enum(tmp_path: Path) -> None:
    SpineWriter(tmp_path).emit(
        source="hook",
        event="capture",
        level="error",
        error_code=ErrorCode.CAPTURE_FAILED,
    )
    rec = _read(tmp_path / ".lore" / "spine.jsonl")[0]
    # Serialized as the enum's string value, not "ErrorCode.CAPTURE_FAILED".
    assert rec["error_code"] == ErrorCode.CAPTURE_FAILED.value
    assert isinstance(rec["error_code"], str)
    validate_envelope(rec)


def test_validate_rejects_freeform_error_code() -> None:
    rec = _template()
    rec["error_code"] = "made-up-string"
    with pytest.raises(ValueError):
        validate_envelope(rec)


def test_validate_rejects_bad_source_and_level() -> None:
    bad_source = _template()
    bad_source["source"] = "wizard"
    with pytest.raises(ValueError):
        validate_envelope(bad_source)
    bad_level = _template()
    bad_level["level"] = "debug"
    with pytest.raises(ValueError):
        validate_envelope(bad_level)


def test_validate_rejects_missing_or_extra_fields() -> None:
    missing = _template()
    del missing["trace_id"]
    with pytest.raises(ValueError):
        validate_envelope(missing)
    extra = _template()
    extra["outcome"] = "leaked-to-top-level"
    with pytest.raises(ValueError):
        validate_envelope(extra)


def test_freeform_strings_live_in_data(tmp_path: Path) -> None:
    """Free-form producer detail belongs in `data`, never as a new top-level key."""
    SpineWriter(tmp_path).emit(
        source="curator",
        event="run-end",
        data={"notes_new": 3, "reason": "cap-tripped", "arbitrary": "text"},
    )
    rec = _read(tmp_path / ".lore" / "spine.jsonl")[0]
    assert rec["data"]["reason"] == "cap-tripped"
    assert set(rec.keys()) == set(ENVELOPE_FIELDS)
    validate_envelope(rec)


def _template() -> dict:
    """A minimal valid envelope for validator rejection tests."""
    return {
        "ts": "2026-07-11T00:00:00Z",
        "v": SCHEMA_VERSION,
        "source": "hook",
        "event": "session-end",
        "level": "info",
        "trace_id": None,
        "session_id": None,
        "run_id": None,
        "wiki": None,
        "scope": None,
        "error_code": None,
        "data": {},
    }


# ---------------------------------------------------------------------------
# AC5 — schema version present
# ---------------------------------------------------------------------------


def test_schema_version_present_and_int(tmp_path: Path) -> None:
    SpineWriter(tmp_path).emit(source="hook", event="e")
    rec = _read(tmp_path / ".lore" / "spine.jsonl")[0]
    assert isinstance(rec["v"], int)
    assert rec["v"] == SCHEMA_VERSION


# ---------------------------------------------------------------------------
# AC2 — atomic appends, concurrency, flock-guarded rotation
# ---------------------------------------------------------------------------


def test_append_is_single_write_under_pipe_buf(tmp_path: Path) -> None:
    """Each record must fit in one <=PIPE_BUF os.write() (interleave-safe)."""
    SpineWriter(tmp_path).emit(
        source="hook",
        event="session-end",
        data={"cwd": "/some/long/path", "pid": 12345, "outcome": "spawned-curator"},
    )
    raw = (tmp_path / ".lore" / "spine.jsonl").read_bytes()
    assert len(raw) <= 4096
    assert raw.endswith(b"\n")


def test_concurrent_writers_no_corruption(tmp_path: Path) -> None:
    writer = SpineWriter(tmp_path)
    n = 40

    def emit_many(tag: str) -> None:
        for i in range(n):
            writer.emit(source="hook", event="e", data={"tag": tag, "i": i})

    threads = [threading.Thread(target=emit_many, args=(t,)) for t in ("A", "B", "C")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lines = (tmp_path / ".lore" / "spine.jsonl").read_text().splitlines()
    assert len([x for x in lines if x.strip()]) == 3 * n
    # No interleaving: every line is a standalone parseable, valid envelope.
    for line in lines:
        if line.strip():
            validate_envelope(json.loads(line))


def test_rotation_crosses_threshold(tmp_path: Path) -> None:
    writer = SpineWriter(tmp_path, max_size_mb=1)
    path = tmp_path / ".lore" / "spine.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x" * 1_100_000 + "\n")
    writer.emit(source="hook", event="session-end")
    rotated = tmp_path / ".lore" / "spine.jsonl.1"
    assert rotated.exists()
    assert path.exists()
    assert path.stat().st_size < 2000


# ---------------------------------------------------------------------------
# #190 — janitor-triggered rotation: age or the currently-configured size
# cap, independent of the fixed cap a writer was constructed with.
# ---------------------------------------------------------------------------


def test_janitor_rotate_if_due_by_age(tmp_path: Path) -> None:
    writer = SpineWriter(tmp_path)
    path = tmp_path / ".lore" / "spine.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    old_rec = {**_template(), "ts": "2020-01-01T00:00:00Z"}
    path.write_text(json.dumps(old_rec) + "\n")
    rotated = writer.janitor_rotate_if_due(max_age_days=7, max_size_mb=10)
    assert rotated is True
    assert (tmp_path / ".lore" / "spine.jsonl.1").exists()
    # Rotation is a rename; the hot path only reappears on the next emit().
    assert not path.exists()


def test_janitor_rotate_if_due_by_configured_size(tmp_path: Path) -> None:
    writer = SpineWriter(tmp_path, max_size_mb=10)  # writer's own fixed cap
    path = tmp_path / ".lore" / "spine.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x" * 2000 + "\n")
    # Janitor re-checks against a tighter, currently-configured cap.
    rotated = writer.janitor_rotate_if_due(max_age_days=365, max_size_mb=0.001)
    assert rotated is True
    assert (tmp_path / ".lore" / "spine.jsonl.1").exists()


def test_janitor_rotate_if_due_noop_within_window(tmp_path: Path) -> None:
    writer = SpineWriter(tmp_path)
    path = tmp_path / ".lore" / "spine.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh_rec = {**_template(), "ts": _now_iso()}
    path.write_text(json.dumps(fresh_rec) + "\n")
    rotated = writer.janitor_rotate_if_due(max_age_days=7, max_size_mb=10)
    assert rotated is False
    assert not (tmp_path / ".lore" / "spine.jsonl.1").exists()


def test_janitor_rotate_if_due_missing_file_is_noop(tmp_path: Path) -> None:
    writer = SpineWriter(tmp_path)
    assert writer.janitor_rotate_if_due(max_age_days=7, max_size_mb=10) is False


def test_rotation_race_no_data_loss(tmp_path: Path) -> None:
    writer = SpineWriter(tmp_path, max_size_mb=1)
    path = tmp_path / ".lore" / "spine.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x" * 1_100_000 + "\n")
    errors: list[Exception] = []

    def emit_one(name: str) -> None:
        try:
            writer.emit(source="hook", event=name)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    t1 = threading.Thread(target=emit_one, args=("session-end",))
    t2 = threading.Thread(target=emit_one, args=("session-start",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert not errors
    rotated = tmp_path / ".lore" / "spine.jsonl.1"
    all_lines: list[str] = []
    if rotated.exists():
        all_lines += [x for x in rotated.read_text().splitlines() if x.strip()]
    all_lines += [x for x in path.read_text().splitlines() if x.strip()]
    recs = []
    for x in all_lines:
        try:
            recs.append(json.loads(x))
        except Exception:  # noqa: BLE001
            continue
    assert len(recs) == 2
    assert {r["event"] for r in recs} == {"session-end", "session-start"}


# ---------------------------------------------------------------------------
# AC4 — write failure degrades to marker, never raises
# ---------------------------------------------------------------------------


def test_write_failure_touches_marker(tmp_path: Path, monkeypatch) -> None:
    import os as _os

    real_open = _os.open

    def faulty_open(path, *args, **kwargs):
        if str(path).endswith("spine.jsonl"):
            raise OSError("disk full")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(_os, "open", faulty_open)
    SpineWriter(tmp_path).emit(source="hook", event="session-end")  # must not raise
    assert (tmp_path / ".lore" / "spine-failed.marker").exists()


# ---------------------------------------------------------------------------
# read_spine helper
# ---------------------------------------------------------------------------


def test_read_spine_filters_by_source(tmp_path: Path) -> None:
    w = SpineWriter(tmp_path)
    w.emit(source="hook", event="a")
    w.emit(source="curator", event="b")
    w.emit(source="hook", event="c")
    assert [r["event"] for r in read_spine(tmp_path, source="hook")] == ["a", "c"]
    assert len(read_spine(tmp_path)) == 3
    assert read_spine(tmp_path / "nonexistent") == []


def test_read_spine_skips_malformed_lines(tmp_path: Path) -> None:
    path = tmp_path / ".lore" / "spine.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{not json\n{"source":"hook","event":"ok"}\n\n')
    recs = read_spine(tmp_path, source="hook")
    assert [r["event"] for r in recs] == ["ok"]


# ---------------------------------------------------------------------------
# hook producer adapter — maps the legacy flat kwargs onto the envelope
# ---------------------------------------------------------------------------


def test_emit_hook_event_maps_scope_and_outcome(tmp_path: Path) -> None:
    emit_hook_event(
        tmp_path,
        event="session-end",
        outcome="spawned-curator",
        scope={"wiki": "private", "scope": "lore"},
        run_id="2026-04-20T14-32-05-a1b2c3",
        integration="saiyajin",
        duration_ms=47,
        pending_after=3,
    )
    rec = _read(tmp_path / ".lore" / "spine.jsonl")[0]
    validate_envelope(rec)
    assert rec["source"] == "hook"
    assert rec["event"] == "session-end"
    assert rec["level"] == "info"
    assert rec["wiki"] == "private"
    assert rec["scope"] == "lore"
    assert rec["run_id"] == "2026-04-20T14-32-05-a1b2c3"
    # Everything the envelope has no slot for lands in data.
    assert rec["data"]["outcome"] == "spawned-curator"
    assert rec["data"]["integration"] == "saiyajin"
    assert rec["data"]["duration_ms"] == 47
    assert rec["data"]["pending_after"] == 3


def test_emit_hook_event_error_outcome_is_error_level(tmp_path: Path) -> None:
    emit_hook_event(
        tmp_path,
        event="session-end",
        outcome="error",
        error={"type": "RuntimeError", "message": "boom"},
        error_code=ErrorCode.CAPTURE_FAILED,
    )
    rec = _read(tmp_path / ".lore" / "spine.jsonl")[0]
    validate_envelope(rec)
    assert rec["level"] == "error"
    assert rec["error_code"] == ErrorCode.CAPTURE_FAILED.value
    assert rec["data"]["error"]["type"] == "RuntimeError"


def test_emit_hook_event_warning_outcome_is_warn_level(tmp_path: Path) -> None:
    emit_hook_event(tmp_path, event="spawn-throttle", outcome="prior-runaway")
    rec = _read(tmp_path / ".lore" / "spine.jsonl")[0]
    assert rec["level"] == "warn"
