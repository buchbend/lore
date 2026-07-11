from pathlib import Path

from lore_core.run_retention import enforce_retention
from lore_core.spine import read_spine, validate_envelope


def _mk_run(dir_: Path, name: str, size: int = 500) -> Path:
    p = dir_ / f"{name}.jsonl"
    p.write_text("x" * size + "\n")
    return p


def test_count_cap_deletes_oldest(tmp_path):
    runs = tmp_path / ".lore" / "runs"
    runs.mkdir(parents=True)
    for i in range(205):
        _mk_run(runs, f"2026-04-{i:02d}T10-00-00-xxxxxx")
    enforce_retention(tmp_path, keep=200, max_total_mb=9999, keep_trace=30)
    remaining = [p for p in runs.glob("*.jsonl") if not p.name.endswith(".trace.jsonl")]
    assert len(remaining) == 200


def test_mb_cap_deletes_until_under_cap(tmp_path):
    runs = tmp_path / ".lore" / "runs"
    runs.mkdir(parents=True)
    for i in range(60):
        _mk_run(runs, f"2026-04-{i:02d}T10-00-00-xxxxxx", size=2_100_000)
    enforce_retention(tmp_path, keep=1000, max_total_mb=100, keep_trace=30)
    remaining = [p for p in runs.glob("*.jsonl") if not p.name.endswith(".trace.jsonl")]
    total = sum(p.stat().st_size for p in remaining)
    assert total <= 100 * 1024 * 1024


def test_orphan_trace_deleted(tmp_path):
    runs = tmp_path / ".lore" / "runs"
    runs.mkdir(parents=True)
    _mk_run(runs, "2026-04-20T10-00-00-keeper")
    (runs / "2026-04-20T10-00-00-orphan.trace.jsonl").write_text("x\n")
    enforce_retention(tmp_path, keep=200, max_total_mb=9999, keep_trace=30)
    assert not (runs / "2026-04-20T10-00-00-orphan.trace.jsonl").exists()
    assert (runs / "2026-04-20T10-00-00-keeper.jsonl").exists()


def test_trace_cap_deletes_oldest_trace(tmp_path):
    runs = tmp_path / ".lore" / "runs"
    runs.mkdir(parents=True)
    for i in range(35):
        name = f"2026-04-{i:02d}T10-00-00-xxxxxx"
        _mk_run(runs, name)
        (runs / f"{name}.trace.jsonl").write_text("x\n")
    enforce_retention(tmp_path, keep=1000, max_total_mb=9999, keep_trace=30)
    trace_files = list(runs.glob("*.trace.jsonl"))
    assert len(trace_files) == 30


def test_retention_is_best_effort_no_raise(tmp_path, monkeypatch):
    runs = tmp_path / ".lore" / "runs"
    runs.mkdir(parents=True)
    for i in range(205):
        _mk_run(runs, f"2026-04-{i:02d}T10-00-00-xxxxxx")
    real_unlink = Path.unlink

    def bad_unlink(self, *args, **kwargs):
        raise PermissionError("locked")

    monkeypatch.setattr(Path, "unlink", bad_unlink)
    # Should not raise even if every unlink fails.
    enforce_retention(tmp_path, keep=200, max_total_mb=9999, keep_trace=30)
    monkeypatch.setattr(Path, "unlink", real_unlink)


# ---------------------------------------------------------------------------
# #190 — deletions are logged, delete failures are warnings, never silence.
# ---------------------------------------------------------------------------


def test_deletion_emits_janitor_spine_event(tmp_path):
    runs = tmp_path / ".lore" / "runs"
    runs.mkdir(parents=True)
    for i in range(205):
        _mk_run(runs, f"2026-04-{i:02d}T10-00-00-xxxxxx")
    enforce_retention(tmp_path, keep=200, max_total_mb=9999, keep_trace=30)
    events = read_spine(tmp_path, source="janitor")
    deletes = [e for e in events if e["event"] == "retention-delete"]
    assert len(deletes) == 5  # 205 - keep=200
    for e in deletes:
        validate_envelope(e)
        assert e["level"] == "info"
        assert e["data"]["family"] == "run-archival"


def test_delete_failure_emits_warn_spine_event(tmp_path, monkeypatch):
    runs = tmp_path / ".lore" / "runs"
    runs.mkdir(parents=True)
    for i in range(205):
        _mk_run(runs, f"2026-04-{i:02d}T10-00-00-xxxxxx")

    def bad_unlink(self, *args, **kwargs):
        raise PermissionError("locked")

    monkeypatch.setattr(Path, "unlink", bad_unlink)
    enforce_retention(tmp_path, keep=200, max_total_mb=9999, keep_trace=30)
    events = read_spine(tmp_path, source="janitor")
    failures = [e for e in events if e["event"] == "retention-delete-failed"]
    assert failures, "a delete failure must emit a warn-level spine event, not silence"
    for e in failures:
        validate_envelope(e)
        assert e["level"] == "warn"
        assert e["data"]["family"] == "run-archival"
