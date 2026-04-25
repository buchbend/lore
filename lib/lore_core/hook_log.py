"""Append-only hook-event log at $LORE_ROOT/.lore/hook-events.jsonl.

One record per hook invocation. Hot-path; must not raise.

Concurrency design (audited 2026-04-26 in Phase 3):

* **Appends are POSIX-atomic.** ``emit()`` opens the log with
  ``O_APPEND | O_CREAT`` and writes the JSONL record in a single
  ``os.write()`` call. POSIX guarantees that writes ≤ ``PIPE_BUF``
  (4096 bytes on Linux) to an O_APPEND file don't interleave between
  concurrent writers. Hook records are well under that limit, so
  N concurrent Claude sessions on the same vault append safely
  without a lock.
* **Rotation is flock-guarded.** Two concurrent hooks both seeing
  ``size > max_size`` would otherwise race on ``os.replace()`` and
  lose a rotation window. ``_maybe_rotate()`` takes a non-blocking
  ``fcntl.LOCK_EX`` on a sibling ``hook-events.rotate.lock`` file;
  losers skip the rotation this cycle (the next emit will retry).
* **Failures are observable.** Any ``OSError`` in ``emit()`` touches
  ``$LORE_ROOT/.lore/hook-log-failed.marker`` so ``lore doctor`` and
  ``lore status`` can surface "your hook log writes are failing"
  without crashing the hook itself.
"""

from __future__ import annotations

import fcntl
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _ppid_cmd() -> str | None:
    """Return /proc/<ppid>/cmdline as space-joined string, or None on non-Linux/error."""
    try:
        ppid = os.getppid()
        data = Path(f"/proc/{ppid}/cmdline").read_bytes()
        if not data:
            return None
        # cmdline is NUL-separated; replace NULs with spaces and strip trailing.
        return data.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
    except (OSError, ValueError):
        return None


class HookEventLogger:
    """Single-record appender for hook-events.jsonl.

    I/O-free at construction time — no file is opened until emit().
    """

    def __init__(self, lore_root: Path, *, max_size_mb: int = 10):
        self._dir = lore_root / ".lore"
        self._path = self._dir / "hook-events.jsonl"
        self._rotated = self._dir / "hook-events.jsonl.1"
        self._rotate_lock = self._dir / "hook-events.rotate.lock"
        self._marker = self._dir / "hook-log-failed.marker"
        self._max_size = max_size_mb * 1024 * 1024

    def emit(self, **record: Any) -> None:
        """Append one record. Never raises."""
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._maybe_rotate()
            payload = {
                "schema_version": 2,
                "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                **record,
            }
            payload.setdefault("error", None)
            line = (json.dumps(payload) + "\n").encode()
            fd = os.open(self._path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
            try:
                os.write(fd, line)
            finally:
                os.close(fd)
        except OSError:
            self._touch_marker()

    def _maybe_rotate(self) -> None:
        if not self._path.exists():
            return
        try:
            size = self._path.stat().st_size
        except OSError:
            return
        if size < self._max_size:
            return
        # Non-blocking flock — loser skips rotation this cycle.
        try:
            with self._rotate_lock.open("a") as lock_f:
                try:
                    fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    return  # another process is rotating; skip
                # Re-check under lock.
                try:
                    if self._path.stat().st_size < self._max_size:
                        return
                except OSError:
                    return
                os.replace(self._path, self._rotated)
        except OSError:
            pass

    def _touch_marker(self) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._marker.touch(exist_ok=True)
            os.utime(self._marker, None)
        except OSError:
            pass
