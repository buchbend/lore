"""File locks — one flock primitive, one spawn-lock convention over it.

``flocked`` is the single ``fcntl.flock`` context manager every caller
uses. ``try_acquire_spawn_lock`` wraps it with the per-role path
convention that serializes the *decision to spawn* a detached process:
held only across the ``Popen`` call, and auto-released by the kernel on
process exit, so a crashed spawner never orphans the lock (issue #17).
"""

from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def flocked(path: Path, *, blocking: bool = True):
    """``fcntl.flock`` context manager — single source of truth.

    Locks ``path`` with ``LOCK_EX``. Yields ``True`` once acquired
    (``blocking=True``, default) or yields ``True``/``False`` immediately
    based on whether ``LOCK_NB`` succeeded (``blocking=False``).

    Always closes the file descriptor on exit; releases the lock if
    held. Kernel semantics: flock is released when the holding process
    exits (normal or abnormal), so a crashed holder never leaves the
    lock held — no stale-lock recovery is needed.

    Replaces three near-identical call sites (this module's spawn lock,
    ``spine`` rotation, ``managed_files._flocked``).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd: int | None = None
    held = False
    try:
        try:
            fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
        except OSError:
            yield False
            return
        flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(fd, flags)
            held = True
        except BlockingIOError:
            held = False
        yield held
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


@contextmanager
def try_acquire_spawn_lock(lore_root: Path, role: str):
    """Non-blocking per-role spawn lock; yields (held: bool, stamp_path: Path).

    Wraps :func:`flocked` with the spawn-lock-specific path convention
    and the (held, stamp_path) tuple shape that callers depend on for
    cooldown bookkeeping.
    """
    lock_path = lore_root / ".lore" / f"curator-{role}.spawn.lock"
    stamp_path = lore_root / ".lore" / f"curator-{role}.spawn.stamp"
    with flocked(lock_path, blocking=False) as held:
        yield (held, stamp_path)
