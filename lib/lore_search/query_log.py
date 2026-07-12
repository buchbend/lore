"""Append-only query/event log at $LORE_CACHE/query-log.jsonl.

One record per FTS query (and per reindex-skip event from the MCP
server). Hot-path; must not raise.

Concurrency design mirrors :mod:`lore_core.spine` (audited 2026-04-26):

* **Appends are POSIX-atomic for records ≤ PIPE_BUF (4096 bytes on
  Linux).** ``emit()`` opens the log with ``O_APPEND | O_CREAT`` and
  writes one JSONL record in a single ``os.write()``. Query records
  include a ``results`` array — callers cap it at the user-visible
  top-k (not the internal ``k * 3`` over-fetch) so records stay well
  under the limit. Records exceeding 4096 bytes (very long paths +
  large k) may interleave under concurrent writers; rare for
  STDIO-MCP, documented limitation.
* **Rotation is flock-guarded.** ``_maybe_rotate()`` takes a
  non-blocking ``LOCK_EX`` on a sibling lock file at 10 MB; losers
  skip the cycle.
* **Failures are observable.** Any ``OSError`` in ``emit()`` touches
  ``query-log-failed.marker`` so future ``lore status`` /``lore doctor``
  surfacing can flag broken logging without crashing the query.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class QueryLogger:
    """Single-record appender for query-log.jsonl.

    I/O-free at construction time — no file is opened until emit().
    """

    def __init__(self, cache_dir: Path, *, max_size_mb: int = 10):
        self._dir = cache_dir
        self._path = self._dir / "query-log.jsonl"
        self._rotated = self._dir / "query-log.jsonl.1"
        self._rotate_lock = self._dir / "query-log.rotate.lock"
        self._marker = self._dir / "query-log-failed.marker"
        self._max_size = max_size_mb * 1024 * 1024

    @property
    def path(self) -> Path:
        return self._path

    def emit(self, **record: Any) -> None:
        """Append one record. Never raises."""
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._maybe_rotate()
            payload = {
                "schema_version": 1,
                "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                **record,
            }
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
        from lore_core.lockfile import flocked
        try:
            with flocked(self._rotate_lock, blocking=False) as held:
                if not held:
                    return
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


def get_logger() -> QueryLogger:
    """Construct a QueryLogger pointed at the resolved cache dir.

    Cheap — no I/O at construction. Callers may safely instantiate per
    request (mirrors how fts.py instantiates its sqlite connection).
    """
    from lore_search.fts import _cache_dir
    return QueryLogger(_cache_dir())
