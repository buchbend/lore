"""Atomic mkdir-based lockfile for curator serialization."""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path


class LockContendedError(Exception):
    """Raised when the lock is held by another process and timeout expired."""


@contextmanager
def curator_lock(
    lore_root: Path,
    *,
    timeout: float = 0.0,
    stale_after: float = 3600.0,
    poll_interval: float = 0.1,
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

    try:
        yield lock_dir
    finally:
        try:
            os.rmdir(lock_dir)
        except (FileNotFoundError, OSError):
            # Another contender may have cleaned it up; don't mask errors.
            pass
