"""Atomic mkdir-based lockfile for curator serialization.

This module provides two lock primitives that serve distinct purposes:

* ``curator_lock`` (mkdir-based): serializes the *work* of a curator run —
  held for the full duration of note writing, ledger updates, etc. Stale
  after 1h of no mtime touch.

* ``try_acquire_spawn_lock`` (fcntl.flock-based): serializes the *decision
  to spawn* a detached curator — held only across the ``Popen`` call. Uses
  kernel-level ``flock`` so the lock auto-releases on process exit, which
  means a crashed spawner never orphans the lock. This is the correct
  primitive for the spawn-throttle race (see issue #17).
"""

from __future__ import annotations

import fcntl
import json
import os
import socket
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


class LockContendedError(Exception):
    """Raised when the lock is held by another process and timeout expired."""


@contextmanager
def try_acquire_spawn_lock(lore_root: Path, role: str):
    """Non-blocking per-role spawn lock; yields (held: bool, stamp_path: Path).

    Uses ``fcntl.flock(LOCK_EX | LOCK_NB)`` on
    ``$LORE_ROOT/.lore/curator-<role>.spawn.lock``. If another process holds
    the lock, yields (False, stamp_path) immediately — caller should give up.
    If acquired, yields (True, stamp_path) and releases on context exit.

    Kernel semantics: flock is released when the holding process exits
    (normal or abnormal), so a crashed spawner never leaves the lock held.
    No stale-lock recovery is needed.

    The stamp_path is used by callers for cooldown bookkeeping (read at
    lock-acquire, written after a successful Popen). It is NOT touched by
    this function.
    """
    lock_path = lore_root / ".lore" / f"curator-{role}.spawn.lock"
    stamp_path = lore_root / ".lore" / f"curator-{role}.spawn.stamp"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    fd: int | None = None
    held = False
    try:
        try:
            fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
        except OSError:
            yield (False, stamp_path)
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            held = True
        except BlockingIOError:
            held = False
        yield (held, stamp_path)
    finally:
        if fd is not None:
            if held:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            try:
                os.close(fd)
            except OSError:
                pass


def read_lock_holder(lore_root: Path) -> dict | None:
    """Return the current lock holder payload, or None if no lock / unreadable."""
    owner = lore_root / ".lore" / "curator.lock" / "owner.json"
    if not owner.exists():
        return None
    try:
        return json.loads(owner.read_text())
    except (OSError, json.JSONDecodeError):
        return None


@contextmanager
def curator_lock(
    lore_root: Path,
    *,
    timeout: float = 0.0,
    stale_after: float = 3600.0,
    poll_interval: float = 0.1,
    run_id: str | None = None,
):
    """Atomic mkdir lockfile at `<lore_root>/.lore/curator.lock`.

    Yields when the lock is acquired; releases on exit (even on
    exception). Raises `LockContendedError` if the lock is held by
    another process and either `timeout` expired or `timeout==0`.

    Stale-detection: a lock whose directory mtime is older than
    `stale_after` seconds is considered abandoned; it is removed and
    the caller acquires fresh. This guards against crashed runs.

    Uses `mkdir` + `rmdir` (atomic on POSIX). The parent `.lore/` dir
    is created if missing, outside the lock.

    On successful acquire, writes owner.json with provenance metadata.
    Removed on release.
    """
    lock_dir = lore_root / ".lore" / "curator.lock"
    lock_dir.parent.mkdir(parents=True, exist_ok=True)

    deadline = time.monotonic() + timeout if timeout > 0 else None

    while True:
        try:
            os.mkdir(lock_dir)
            # Acquired
            break
        except FileExistsError:
            # Held — check staleness
            try:
                age = time.time() - lock_dir.stat().st_mtime
            except FileNotFoundError:
                # Race: released between stat and mkdir. Retry.
                continue
            if age > stale_after:
                # Stale — remove and retry. Another contender may race here;
                # the worst case is one extra iteration.
                try:
                    os.rmdir(lock_dir)
                except (FileNotFoundError, OSError):
                    pass
                continue
            # Not stale; wait or raise
            if deadline is None or time.monotonic() >= deadline:
                raise LockContendedError(
                    f"curator lock held (age={age:.1f}s, stale_after={stale_after:.0f}s)"
                )
            time.sleep(poll_interval)

    # Write owner.json with provenance — best-effort; lock is already acquired.
    owner_path = lock_dir / "owner.json"
    owner_payload = {
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "started_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "run_id": run_id,
        "cmd": " ".join(sys.argv)[:500],
    }
    try:
        owner_path.write_text(json.dumps(owner_payload))
    except OSError:
        pass  # best-effort; lock still acquired

    try:
        yield lock_dir
    finally:
        # Release — remove owner.json first, then lock_dir (atomic on POSIX).
        try:
            owner_path.unlink()
        except (FileNotFoundError, OSError):
            pass
        try:
            os.rmdir(lock_dir)
        except (FileNotFoundError, OSError):
            # Another contender may have cleaned it up; don't mask errors.
            pass
