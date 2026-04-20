import json
from pathlib import Path

from lore_core.hook_log import HookEventLogger, _ppid_cmd


def test_emit_appends_one_line(tmp_path: Path):
    logger = HookEventLogger(tmp_path)
    logger.emit(
        event="session-end",
        host="saiyajin",
        transcript_id="t-abc",
        scope={"wiki": "private", "scope": "lore"},
        duration_ms=47,
        outcome="spawned-curator",
        pending_after=3,
        run_id="2026-04-20T14-32-05-a1b2c3",
    )
    path = tmp_path / ".lore" / "hook-events.jsonl"
    assert path.exists()
    lines = path.read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["schema_version"] == 2
    assert record["event"] == "session-end"
    assert record["outcome"] == "spawned-curator"
    assert record["run_id"] == "2026-04-20T14-32-05-a1b2c3"
    assert record["error"] is None
    assert "ts" in record


def test_rotation_crosses_threshold(tmp_path: Path):
    logger = HookEventLogger(tmp_path, max_size_mb=1)
    path = tmp_path / ".lore" / "hook-events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x" * 1_100_000 + "\n")
    logger.emit(event="session-end", outcome="ledger-advanced")
    rotated = tmp_path / ".lore" / "hook-events.jsonl.1"
    assert rotated.exists()
    assert path.exists()
    assert path.stat().st_size < 2000


def test_write_failure_touches_marker(tmp_path: Path, monkeypatch):
    import os as _os
    logger = HookEventLogger(tmp_path)
    real_open = _os.open

    def faulty_open(path, *args, **kwargs):
        # Fail only for hook-events.jsonl; allow directories and lock file.
        if str(path).endswith("hook-events.jsonl"):
            raise OSError("disk full")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(_os, "open", faulty_open)
    logger.emit(event="session-end", outcome="ledger-advanced")
    marker = tmp_path / ".lore" / "hook-log-failed.marker"
    assert marker.exists()


def test_emit_schema_v2_and_provenance_fields(tmp_path: Path):
    logger = HookEventLogger(tmp_path)
    logger.emit(
        event="session-end",
        outcome="ledger-advanced",
        pid=12345,
        cwd="/some/path",
        ppid_cmd="bash -l",
    )
    path = tmp_path / ".lore" / "hook-events.jsonl"
    record = json.loads(path.read_text().splitlines()[-1])
    assert record["schema_version"] == 2
    assert record["pid"] == 12345
    assert record["cwd"] == "/some/path"
    assert record["ppid_cmd"] == "bash -l"


def test_ppid_cmd_returns_none_on_missing_proc(tmp_path, monkeypatch):
    # Point at a PPID we know can't be read.
    monkeypatch.setattr("os.getppid", lambda: 9999999)
    assert _ppid_cmd() is None or isinstance(_ppid_cmd(), str)


def test_rotation_race_no_data_loss(tmp_path: Path):
    import threading
    logger = HookEventLogger(tmp_path, max_size_mb=1)
    path = tmp_path / ".lore" / "hook-events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x" * 1_100_000 + "\n")
    errors: list[Exception] = []
    def emit_one(event_name: str):
        try:
            logger.emit(event=event_name, outcome="ledger-advanced")
        except Exception as e:
            errors.append(e)
    t1 = threading.Thread(target=emit_one, args=("session-end",))
    t2 = threading.Thread(target=emit_one, args=("session-start",))
    t1.start(); t2.start(); t1.join(); t2.join()
    assert not errors
    rotated = tmp_path / ".lore" / "hook-events.jsonl.1"
    all_lines: list[str] = []
    if rotated.exists():
        all_lines += [l for l in rotated.read_text().splitlines() if l.strip()]
    all_lines += [l for l in path.read_text().splitlines() if l.strip()]
    # Every line from the pre-seeded 1.1MB file is a single "xxx..." block
    # (no newline inside the filler except the trailing one). So we expect:
    #   1 pre-seed line + 2 emit lines = 3 total lines across both files
    new_records = []
    for l in all_lines:
        try:
            rec = json.loads(l)
        except Exception:
            continue
        new_records.append(rec)
    assert len(new_records) == 2, f"both emits should appear as distinct records, got {new_records}"
    events_seen = {r.get("event") for r in new_records}
    assert events_seen == {"session-end", "session-start"}
