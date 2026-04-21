"""Task 3: Curator C pre/post-diff audit log.

Every C run writes a date-stamped log entry with run_id, unified diff
per modified note, and a summary table. Enables rollback auditing.

Design points (per review):
- Date-stamped file name: curator-c.diff.YYYY-MM-DD.log
- Each entry carries run_id (cross-refs runs/<id>.jsonl)
- 90-day retention (older files unlinked on next run)
- 10 MB rotation (rotates to .1)
- Zero-change runs → single-line marker, not full empty entry
- Permission-denied → warning event, no crash
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lore_curator.curator_c_diff import (
    prune_old_diff_logs,
    write_diff_log_entry,
)


def _lore_root(tmp_path: Path) -> Path:
    (tmp_path / ".lore").mkdir()
    return tmp_path


def _read_log(path: Path) -> str:
    return path.read_text()


def _snapshot(files: dict[str, str]) -> dict[str, str]:
    """{relative_path: content_str} — accepts empty for 'no file'."""
    return dict(files)


# ---------------------------------------------------------------------------
# Basic capture
# ---------------------------------------------------------------------------


def test_diff_log_captures_frontmatter_changes(tmp_path: Path) -> None:
    lore_root = _lore_root(tmp_path)

    before = _snapshot({"note.md": "---\nstatus: active\n---\n\nBody\n"})
    after = _snapshot({"note.md": "---\nstatus: stale\n---\n\nBody\n"})

    write_diff_log_entry(
        lore_root,
        run_id="abc123",
        snapshot_before=before,
        snapshot_after=after,
        dry_run=False,
        summary={"staleness_flips": 1},
    )

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    log = lore_root / ".lore" / f"curator-c.diff.{today}.log"
    assert log.exists()
    content = _read_log(log)
    assert "abc123" in content
    assert "-status: active" in content
    assert "+status: stale" in content


def test_diff_log_dry_run_writes_entry(tmp_path: Path) -> None:
    lore_root = _lore_root(tmp_path)
    before = _snapshot({"a.md": "old\n"})
    after = _snapshot({"a.md": "new\n"})

    write_diff_log_entry(
        lore_root, run_id="dry", snapshot_before=before, snapshot_after=after,
        dry_run=True, summary={},
    )

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    log = lore_root / ".lore" / f"curator-c.diff.{today}.log"
    content = _read_log(log)
    assert "dry" in content
    assert "dry-run" in content.lower() or "DRY-RUN" in content


def test_diff_log_daily_append(tmp_path: Path) -> None:
    """Two runs same day → two entries, distinct run_ids."""
    lore_root = _lore_root(tmp_path)
    before = _snapshot({"a.md": "x\n"})
    after = _snapshot({"a.md": "y\n"})

    write_diff_log_entry(lore_root, run_id="aaa", snapshot_before=before,
                         snapshot_after=after, dry_run=False, summary={})
    write_diff_log_entry(lore_root, run_id="bbb", snapshot_before=before,
                         snapshot_after=after, dry_run=False, summary={})

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    log = lore_root / ".lore" / f"curator-c.diff.{today}.log"
    content = _read_log(log)
    assert "aaa" in content
    assert "bbb" in content
    # Two run headers, delimited.
    assert content.count("run=") >= 2


def test_diff_log_no_op_writes_single_line_marker(tmp_path: Path) -> None:
    """Zero-change run → single-line marker, not a full empty entry."""
    lore_root = _lore_root(tmp_path)
    # Identical snapshots — no changes.
    snap = _snapshot({"note.md": "unchanged\n"})

    write_diff_log_entry(lore_root, run_id="noop", snapshot_before=snap,
                         snapshot_after=snap, dry_run=False, summary={})

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    log = lore_root / ".lore" / f"curator-c.diff.{today}.log"
    content = _read_log(log)
    noop_lines = [l for l in content.splitlines() if "noop" in l and "no-op" in l]
    assert noop_lines, f"expected a single-line no-op marker; got:\n{content}"


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


def test_diff_log_90d_retention(tmp_path: Path) -> None:
    """Logs older than 90 days are deleted by prune_old_diff_logs."""
    import os as _os
    lore_root = _lore_root(tmp_path)
    log_dir = lore_root / ".lore"

    # Create two log files: one 100d old, one 30d old.
    old_log = log_dir / "curator-c.diff.2000-01-01.log"
    old_log.write_text("ancient\n")
    old_mtime = time.time() - 100 * 86400
    _os.utime(old_log, (old_mtime, old_mtime))

    new_log = log_dir / f"curator-c.diff.{datetime.now(UTC).strftime('%Y-%m-%d')}.log"
    new_log.write_text("recent\n")

    prune_old_diff_logs(lore_root, retention_days=90)

    assert not old_log.exists(), "100d-old log should be pruned"
    assert new_log.exists(), "recent log should survive"


def test_diff_log_10mb_rotation(tmp_path: Path) -> None:
    """Log ≥ 10 MB rotates to .1 before the new entry appends."""
    lore_root = _lore_root(tmp_path)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    log = lore_root / ".lore" / f"curator-c.diff.{today}.log"
    # Pre-seed with 11 MB of dummy content.
    log.write_text("x" * (11 * 1024 * 1024))

    before = _snapshot({"a.md": "x\n"})
    after = _snapshot({"a.md": "y\n"})
    write_diff_log_entry(lore_root, run_id="fresh", snapshot_before=before,
                         snapshot_after=after, dry_run=False, summary={})

    rotated = lore_root / ".lore" / f"curator-c.diff.{today}.log.1"
    assert rotated.exists(), "old log should rotate to .1"
    assert rotated.stat().st_size >= 10 * 1024 * 1024
    # Fresh log has only the new entry, not the rotated bulk.
    assert log.stat().st_size < 10 * 1024 * 1024


# ---------------------------------------------------------------------------
# Permission denied → warning event
# ---------------------------------------------------------------------------


def test_diff_log_permission_denied_emits_warning_event(tmp_path: Path, monkeypatch) -> None:
    lore_root = _lore_root(tmp_path)

    # Simulate a write failure on the log file only (not the event log).
    real_open = Path.open
    def flaky_open(self, mode="r", *args, **kwargs):
        if "curator-c.diff" in str(self) and any(m in mode for m in ("w", "a", "x")):
            raise PermissionError(f"simulated denial on {self}")
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", flaky_open)

    before = _snapshot({"a.md": "x\n"})
    after = _snapshot({"a.md": "y\n"})
    # Must NOT raise — failure is observable, not crash.
    write_diff_log_entry(lore_root, run_id="perm", snapshot_before=before,
                         snapshot_after=after, dry_run=False, summary={})

    events = lore_root / ".lore" / "hook-events.jsonl"
    assert events.exists()
    lines = [json.loads(l) for l in events.read_text().splitlines() if l.strip()]
    warnings = [
        e for e in lines
        if e.get("event") == "curator-c" and e.get("outcome") == "diff-log-write-failed"
    ]
    assert warnings, f"expected diff-log-write-failed event; got {lines}"
