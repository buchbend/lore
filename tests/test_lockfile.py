"""Tests for curator lockfile with stale detection."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from lore_core.lockfile import LockContendedError, curator_lock


def test_lock_acquires_and_releases(tmp_path: Path) -> None:
    """Acquire lock, confirm dir exists during context; after exit, dir is gone."""
    lock_path = tmp_path / ".lore" / "curator.lock"

    # Lock should not exist before
    assert not lock_path.exists()

    with curator_lock(tmp_path):
        # Lock dir should exist during context
        assert lock_path.exists()
        assert lock_path.is_dir()

    # Lock dir should be gone after context
    assert not lock_path.exists()


def test_lock_contended_raises_when_timeout_zero(tmp_path: Path) -> None:
    """Pre-create lock dir; timeout=0 should raise LockContendedError."""
    lock_dir = tmp_path / ".lore" / "curator.lock"
    lock_dir.mkdir(parents=True, exist_ok=True)

    with pytest.raises(LockContendedError):
        with curator_lock(tmp_path, timeout=0):
            pass


def test_lock_waits_and_acquires_when_timeout_positive(tmp_path: Path) -> None:
    """Spawn thread holding lock for 0.2s; main acquires with timeout=1.0."""
    lock_dir = tmp_path / ".lore" / "curator.lock"
    release_event = threading.Event()
    acquired_event = threading.Event()
    main_acquired = False

    def holder():
        """Acquire lock and hold until event is set."""
        with curator_lock(tmp_path, timeout=1.0):
            acquired_event.set()
            release_event.wait(timeout=0.2)

    thread = threading.Thread(target=holder, daemon=True)
    thread.start()

    # Wait for the holder to acquire the lock
    acquired_event.wait(timeout=2.0)
    assert acquired_event.is_set(), "Holder thread should have acquired lock"

    # Now main thread tries to acquire with timeout
    try:
        with curator_lock(tmp_path, timeout=1.0):
            main_acquired = True
    except LockContendedError:
        pytest.fail("Main thread should have acquired lock after timeout")

    # Signal holder to release
    release_event.set()
    thread.join(timeout=2.0)

    assert main_acquired, "Main thread should have acquired lock"
    assert not thread.is_alive(), "Holder thread should have finished"


def test_stale_lock_reclaimed_after_timeout(tmp_path: Path) -> None:
    """Pre-create lock with mtime 2 hours ago; stale_after=60 should reclaim."""
    lock_dir = tmp_path / ".lore" / "curator.lock"
    lock_dir.mkdir(parents=True, exist_ok=True)

    # Set mtime to 2 hours in the past
    past = time.time() - 7200
    os.utime(lock_dir, (past, past))

    # Should acquire cleanly (stale lock is removed)
    with curator_lock(tmp_path, timeout=0, stale_after=60):
        assert lock_dir.exists()

    assert not lock_dir.exists()


def test_lock_unaffected_by_parent_dir_mtime_change(tmp_path: Path) -> None:
    """Acquire lock; touch parent .lore/ dir; lock's mtime unchanged."""
    lock_dir = tmp_path / ".lore" / "curator.lock"

    with curator_lock(tmp_path):
        # Record the lock dir's mtime while held
        lock_mtime_during = lock_dir.stat().st_mtime

        # Touch the parent .lore directory (simulating git pull)
        parent_dir = lock_dir.parent
        parent_dir.touch()

        # Lock dir's mtime should be unchanged
        lock_mtime_after_touch = lock_dir.stat().st_mtime
        assert lock_mtime_after_touch == lock_mtime_during

    # After release, lock should be gone
    assert not lock_dir.exists()


# ---------------------------------------------------------------------------
# Item B — owner.json provenance tests
# ---------------------------------------------------------------------------


def test_lock_writes_and_removes_owner_json(tmp_path):
    from lore_core.lockfile import curator_lock, read_lock_holder

    assert read_lock_holder(tmp_path) is None
    with curator_lock(tmp_path, timeout=0.0, run_id="test-run-123"):
        holder = read_lock_holder(tmp_path)
        assert holder is not None
        assert holder["pid"] > 0
        assert holder["run_id"] == "test-run-123"
        assert "started_at" in holder
    # Released.
    assert read_lock_holder(tmp_path) is None


def test_lock_contention_does_not_clobber_owner(tmp_path):
    from lore_core.lockfile import curator_lock, LockContendedError, read_lock_holder

    with curator_lock(tmp_path, timeout=0.0, run_id="first"):
        first_holder = read_lock_holder(tmp_path)
        with pytest.raises(LockContendedError):
            with curator_lock(tmp_path, timeout=0.0, run_id="second"):
                pass
        # First holder info is still there.
        still = read_lock_holder(tmp_path)
        assert still == first_holder


def test_read_lock_holder_returns_none_when_no_lock(tmp_path):
    from lore_core.lockfile import read_lock_holder

    assert read_lock_holder(tmp_path) is None


def test_lock_owner_has_expected_fields(tmp_path):
    import socket
    from lore_core.lockfile import curator_lock, read_lock_holder

    with curator_lock(tmp_path, run_id="r-check"):
        h = read_lock_holder(tmp_path)
        assert h["pid"] == os.getpid()
        assert h["host"] == socket.gethostname()
        assert h["run_id"] == "r-check"
        assert "cmd" in h
        assert "started_at" in h
        assert h["started_at"].endswith("Z")


# ---------------------------------------------------------------------------
# Item C — skip records enriched with lock-holder info
# ---------------------------------------------------------------------------


def test_skip_lock_held_includes_holder(tmp_path):
    """run_curator_a skip records contain holder_pid/run_id/age when lock is held."""
    import json
    from lore_core.lockfile import curator_lock
    from lore_curator.session_curator import run_curator_a

    with curator_lock(tmp_path, timeout=0.0, run_id="first-run"):
        run_curator_a(
            lore_root=tmp_path,
            llm_client=None,
            adapter_lookup=lambda h: None,
        )

    runs = list((tmp_path / ".lore" / "runs").glob("*.jsonl"))
    archival = [p for p in runs if not p.name.endswith(".trace.jsonl")]
    assert archival, "Expected at least one run log"
    records = [json.loads(l) for l in archival[0].read_text().splitlines() if l.strip()]
    skip_records = [
        r for r in records
        if r.get("type") == "skip" and r.get("reason") == "lock-held"
    ]
    assert skip_records, "Expected a lock-held skip record"
    s = skip_records[0]
    assert s["holder_pid"]
    assert s["holder_run_id"] == "first-run"
    assert s["holder_age_s"] is not None and s["holder_age_s"] >= 0
