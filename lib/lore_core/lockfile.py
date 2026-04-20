"""Atomic mkdir-based lockfile for curator serialization."""

from __future__ import annotations

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
