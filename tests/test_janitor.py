"""Unified retention janitor tests (issue #190).

Covers the parts that don't already have a home in test_run_retention.py
(run-archival), test_flush_store.py (dead-letter purge) or test_spine.py
(hot->cold rotation): lock contention, cold-tier deletion, orchestration
of all families in one pass, and the queryable usage status.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from lore_core.janitor import janitor_lock, read_janitor_status, run_janitor
from lore_core.lockfile import flocked
from lore_core.root_config import ObservabilityConfig
from lore_core.spine import SpineWriter, read_spine, validate_envelope


def _cfg(**retention_overrides) -> ObservabilityConfig:
    cfg = ObservabilityConfig()
    for k, v in retention_overrides.items():
        setattr(cfg.retention, k, v)
    return cfg


# ---------------------------------------------------------------------------
# Lock-guarded, cheap, daemon-free
# ---------------------------------------------------------------------------


def test_contended_lock_skips_run_without_side_effects(tmp_path: Path):
    with flocked(tmp_path / ".lore" / "janitor.lock"):
        report = run_janitor(tmp_path, _cfg())
    assert report.ran is False


def test_uncontended_run_acquires_and_releases(tmp_path: Path):
    run_janitor(tmp_path, _cfg())
    # Lock released after the pass — a second call can also acquire it.
    with janitor_lock(tmp_path) as held:
        assert held is True


# ---------------------------------------------------------------------------
# Cold tier: age or size cap deletes the rotated file outright.
# ---------------------------------------------------------------------------


def test_cold_tier_deleted_when_past_age_window(tmp_path: Path):
    cold = tmp_path / ".lore" / "spine.jsonl.1"
    cold.parent.mkdir(parents=True, exist_ok=True)
    cold.write_text("x\n")
    old = time.time() - 40 * 86400
    os_utime = __import__("os").utime
    os_utime(cold, (old, old))

    report = run_janitor(tmp_path, _cfg(cold_days=30))
    assert not cold.exists()
    assert report.deleted >= 1


def test_cold_tier_deleted_when_over_size_cap(tmp_path: Path):
    cold = tmp_path / ".lore" / "spine.jsonl.1"
    cold.parent.mkdir(parents=True, exist_ok=True)
    cold.write_text("x" * 2_100_000 + "\n")

    report = run_janitor(tmp_path, _cfg(cold_max_mb=1))
    assert not cold.exists()
    assert report.deleted >= 1


def test_cold_tier_survives_within_window(tmp_path: Path):
    cold = tmp_path / ".lore" / "spine.jsonl.1"
    cold.parent.mkdir(parents=True, exist_ok=True)
    cold.write_text("x\n")

    run_janitor(tmp_path, _cfg(cold_days=30, cold_max_mb=20))
    assert cold.exists()


def test_cold_tier_deletion_emits_janitor_spine_event(tmp_path: Path):
    cold = tmp_path / ".lore" / "spine.jsonl.1"
    cold.parent.mkdir(parents=True, exist_ok=True)
    cold.write_text("x\n")

    run_janitor(tmp_path, _cfg(cold_max_mb=0))
    events = read_spine(tmp_path, source="janitor")
    deletes = [e for e in events if e["event"] == "retention-delete"]
    assert any(e["data"]["family"] == "spine-cold" for e in deletes)
    for e in events:
        validate_envelope(e)


def test_cold_tier_delete_failure_emits_warn_event(tmp_path: Path, monkeypatch):
    cold = tmp_path / ".lore" / "spine.jsonl.1"
    cold.parent.mkdir(parents=True, exist_ok=True)
    cold.write_text("x\n")

    real_unlink = Path.unlink

    def bad_unlink(self, *args, **kwargs):
        raise PermissionError("locked")

    monkeypatch.setattr(Path, "unlink", bad_unlink)
    run_janitor(tmp_path, _cfg(cold_max_mb=0))
    monkeypatch.setattr(Path, "unlink", real_unlink)

    events = read_spine(tmp_path, source="janitor")
    failures = [e for e in events if e["event"] == "retention-delete-failed"]
    assert failures
    assert failures[0]["level"] == "warn"
    assert failures[0]["data"]["family"] == "spine-cold"


# ---------------------------------------------------------------------------
# Hot tier: age-triggered downgrade, using the configured (not hardcoded)
# hook_events.max_size_mb.
# ---------------------------------------------------------------------------


def test_hot_tier_downgrades_by_age(tmp_path: Path):
    hot = tmp_path / ".lore" / "spine.jsonl"
    hot.parent.mkdir(parents=True, exist_ok=True)
    old_rec = {
        "ts": "2020-01-01T00:00:00Z",
        "v": 1,
        "source": "hook",
        "event": "e",
        "level": "info",
        "trace_id": None,
        "session_id": None,
        "run_id": None,
        "wiki": None,
        "scope": None,
        "error_code": None,
        "data": {},
    }
    hot.write_text(json.dumps(old_rec) + "\n")

    run_janitor(tmp_path, _cfg(hot_days=7))
    assert (tmp_path / ".lore" / "spine.jsonl.1").exists()


# ---------------------------------------------------------------------------
# Queryable usage — consumed by `lore status` (#193).
# ---------------------------------------------------------------------------


def test_status_queryable_after_run(tmp_path: Path):
    SpineWriter(tmp_path).emit(source="hook", event="e")
    run_janitor(tmp_path, _cfg())
    status = read_janitor_status(tmp_path)
    assert status is not None
    assert status["hot_bytes"] > 0
    assert "last_run_at" in status


def test_status_none_when_never_run(tmp_path: Path):
    assert read_janitor_status(tmp_path) is None


def test_status_tolerant_of_corrupt_file(tmp_path: Path):
    status_path = tmp_path / ".lore" / "janitor-status.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text("{not json")
    assert read_janitor_status(tmp_path) is None


# ---------------------------------------------------------------------------
# The flush store's one-time teardown (PRD 0013): no reader is left, so the
# janitor clears the directory itself instead of calling FlushStore.purge.
# ---------------------------------------------------------------------------


def test_janitor_deletes_the_flushes_directory(tmp_path: Path):
    flushes = tmp_path / ".lore" / "flushes"
    flushes.mkdir(parents=True)
    (flushes / "stray.json").write_text("{}")

    run_janitor(tmp_path, _cfg())

    assert not flushes.exists()


def test_janitor_runs_clean_without_a_flushes_directory(tmp_path: Path):
    report = run_janitor(tmp_path, _cfg())
    assert report.ran is True
