import json
from pathlib import Path

from lore_core.hook_log import HookEventLogger


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
    assert record["schema_version"] == 1
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
    logger = HookEventLogger(tmp_path)
    real_open = Path.open

    def faulty_open(self, *args, **kwargs):
        if self.name == "hook-events.jsonl":
            raise OSError("disk full")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", faulty_open)
    logger.emit(event="session-end", outcome="ledger-advanced")
    marker = tmp_path / ".lore" / "hook-log-failed.marker"
    assert marker.exists()


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
    all_text = (rotated.read_text() if rotated.exists() else "") + path.read_text()
    assert "session-end" in all_text
    assert "session-start" in all_text
