"""Per-session drain store — append-only event log surfaced by `lore news`.

Each Claude Code session owns a drain file at
``<lore_root>/.lore/drain/<session-id>.jsonl`` that records what Lore
did on its behalf since the session began. A separate `_system.jsonl`
captures events that aren't tied to a specific session (Curator B
surface consolidation, transcript sync, future cross-session work).

Design invariants:

* **Append-only.** Every entry is one line of JSON; the file is never
  rewritten. Crash-safety comes from `O_APPEND` + a hard per-line size
  cap so a partial final line can only ever be a single malformed
  record the reader skips.
* **Line size cap.** ``MAX_DRAIN_LINE`` bytes. On overflow we truncate
  the ``data`` payload and set ``"truncated": true`` so the reader can
  surface "data elided" rather than silently dropping.
* **Fresh-install safe.** ``DrainStore(...)`` creates the parent dir
  eagerly so the first emit doesn't race the filesystem.

Session-id resolution is its own function (:func:`resolve_session_id`)
because the "who is this session?" question has four distinct sources
with a deterministic priority order.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_DRAIN_LINE = 4096  # bytes per record, including trailing newline
SYSTEM_SESSION = "_system"


# Canonical event vocabulary. Kept here so consumers (curators, sync,
# news CLI) reference one list. A ``skip`` path is intentionally
# absent — P3' replaces the LLM verdict with a deterministic append
# rule, so ``noteworthy-false`` has no producer. Reserved for later
# phases (LLM verdict, broadcast): ``noteworthy-false``, ``remote-news``.
EVENT_VOCAB: frozenset[str] = frozenset(
    {"note-filed", "note-appended", "surface-proposed", "transcript-synced"}
)


@dataclass
class DrainEvent:
    ts: datetime
    event: str
    wiki: str | None
    session_id: str
    data: dict[str, Any]
    truncated: bool = False


class DrainStore:
    """Append-only per-session event log rooted at ``.lore/drain/``."""

    def __init__(self, lore_root: Path, session_id: str) -> None:
        self._lore_root = lore_root
        self._session_id = session_id
        self._dir = lore_root / ".lore" / "drain"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / f"{session_id}.jsonl"
        self._cursor_path = self._dir / f"{session_id}.cursor"

    @property
    def path(self) -> Path:
        return self._path

    @property
    def session_id(self) -> str:
        return self._session_id

    def emit(
        self,
        event: str,
        *,
        wiki: str | None = None,
        **data: Any,
    ) -> None:
        """Append one event record to the drain.

        Raises ValueError on an unknown ``event`` name. Caller should
        pass only keys in :data:`EVENT_VOCAB` — unrecognized events
        would be silently written-then-skipped by readers, which is
        the worst of both worlds.
        """
        if event not in EVENT_VOCAB:
            raise ValueError(f"unknown drain event: {event!r}")

        record: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "event": event,
            "wiki": wiki,
            "session_id": self._session_id,
            "data": data,
        }
        line = (json.dumps(record) + "\n").encode("utf-8")
        if len(line) > MAX_DRAIN_LINE:
            # Retry with truncated payload.
            record["data"] = {"truncated_from_keys": sorted(data.keys())}
            record["truncated"] = True
            line = (json.dumps(record) + "\n").encode("utf-8")
            # If even the shell is too big (pathological), drop data entirely.
            if len(line) > MAX_DRAIN_LINE:
                record["data"] = {}
                line = (json.dumps(record) + "\n").encode("utf-8")

        # O_APPEND on a local POSIX fs delivers atomic per-write semantics
        # for writes <= PIPE_BUF (4096 on Linux). Our cap is 4096 bytes
        # INCLUDING the newline, so a single write() is safe. The `with`
        # block close-flushes before return.
        try:
            fd = os.open(str(self._path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
            try:
                os.write(fd, line)
            finally:
                os.close(fd)
        except OSError:
            # Drain is telemetry; never block real work.
            pass

    def read(
        self,
        *,
        since: datetime | None = None,
        limit: int = 50,
    ) -> list[DrainEvent]:
        """Return up to ``limit`` events, optionally filtered by ``since``.

        Malformed lines are silently skipped (see the atomicity note in
        the module docstring). Returns chronological order, oldest first.
        """
        if not self._path.exists():
            return []
        out: list[DrainEvent] = []
        try:
            with self._path.open("r", encoding="utf-8", errors="replace") as fp:
                for raw in fp:
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    ts_raw = obj.get("ts")
                    if not isinstance(ts_raw, str):
                        continue
                    try:
                        ts = datetime.fromisoformat(ts_raw)
                    except ValueError:
                        continue
                    if since is not None and ts < since:
                        continue
                    out.append(
                        DrainEvent(
                            ts=ts,
                            event=str(obj.get("event", "")),
                            wiki=obj.get("wiki"),
                            session_id=str(obj.get("session_id", "")),
                            data=obj.get("data") or {},
                            truncated=bool(obj.get("truncated", False)),
                        )
                    )
        except OSError:
            return []
        # Tail ``limit`` entries.
        if limit > 0:
            out = out[-limit:]
        return out

    def read_cursor(self) -> datetime | None:
        """Return the cursor ts for 'since when have we surfaced events'."""
        if not self._cursor_path.exists():
            return None
        try:
            raw = self._cursor_path.read_text().strip()
        except OSError:
            return None
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None

    def write_cursor(self, ts: datetime) -> None:
        """Atomic cursor write; best-effort (drain is telemetry)."""
        try:
            tmp = self._cursor_path.with_suffix(".cursor.tmp")
            tmp.write_text(ts.isoformat())
            os.replace(tmp, self._cursor_path)
        except OSError:
            pass


def resolve_session_id(
    cwd: Path,
    *,
    hook_payload: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Return ``(session_id, origin)``.

    Priority, stopping at the first hit:

    1. ``hook_payload["session_id"]`` — Claude Code explicitly supplied it.
    2. ``os.environ["CLAUDE_SESSION_ID"]`` — set by the harness.
    3. Newest transcript under ``~/.claude/projects/<encoded-cwd>/`` whose
       mtime is within the last 2 minutes (heuristic: Claude is actively
       writing to it, so it's probably our session).
    4. ``pid-<getpid()>`` fallback. ``origin`` is tagged so readers can
       tell "we know who this is" from "we guessed."

    Never raises; the fallback is always available.
    """
    if hook_payload and isinstance(hook_payload, dict):
        sid = hook_payload.get("session_id")
        if isinstance(sid, str) and sid:
            return sid, "hook-payload"

    env = os.environ.get("CLAUDE_SESSION_ID")
    if env:
        return env, "env"

    # Heuristic: newest transcript within 2 minutes.
    try:
        encoded = str(Path(cwd).resolve()).replace("/", "-")
        projects = Path.home() / ".claude" / "projects" / encoded
        if projects.exists():
            now = time.time()
            newest: tuple[float, str] | None = None
            for p in projects.glob("*.jsonl"):
                try:
                    m = p.stat().st_mtime
                except OSError:
                    continue
                if now - m > 120:
                    continue
                if newest is None or m > newest[0]:
                    newest = (m, p.stem)
            if newest is not None:
                return newest[1], "transcript-freshness"
    except OSError:
        pass

    return f"pid-{os.getpid()}", "pid-fallback"
