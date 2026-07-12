"""Per-session drain store — append-only event log surfaced by `lore status`.

Drain events — what Lore did on a session's behalf, surfaced by the
"news" section of `lore status` — are emitted onto the unified event
spine with ``source="drain"``. Each event carries the resolving
``session_id`` so per-session and ``_system`` streams stay separable on
one shared log. There is no longer a per-session ``<session-id>.jsonl``
writer; :class:`DrainStore` is a thin producer/reader adapter over the
spine, keeping only the news *cursor* files under ``.lore/drain/``.

Design invariants:

* **On the spine.** ``emit`` appends one envelope via
  :class:`~lore_core.spine.SpineWriter` (POSIX-atomic ``O_APPEND``);
  ``read`` filters the spine to ``source="drain"`` and this session's id.
* **Never raises, but visible.** A failed write is swallowed by the spine
  writer *and* leaves the ``spine-failed.marker`` — a dropped drain event
  is no longer invisible.
* **Fresh-install safe.** ``DrainStore(...)`` creates ``.lore/drain/``
  eagerly (for the cursor files) so the first read doesn't race the fs.

Session-id resolution is its own function (:func:`resolve_session_id`)
because the "who is this session?" question has four distinct sources
with a deterministic priority order.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lore_core.spine import SpineWriter, read_spine

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
        # `.lore/drain/` now holds only the news cursor files; drain *events*
        # live on the shared spine. Created eagerly so a cursor read/write
        # never races the filesystem on a fresh install.
        self._dir = lore_root / ".lore" / "drain"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._cursor_path = self._dir / f"{session_id}.cursor"

    @property
    def session_id(self) -> str:
        return self._session_id

    def emit(
        self,
        event: str,
        *,
        wiki: str | None = None,
        trace_id: str | None = None,
        **data: Any,
    ) -> None:
        """Emit one drain event onto the spine (``source="drain"``).

        Raises ValueError on an unknown ``event`` name, or when a
        non-``transcript-synced`` event is targeted at the system drain
        (``SYSTEM_SESSION``) — the shared stream, where per-note events would
        haunt every future SessionStart. The spine write itself never raises:
        a failure is swallowed *and* marked (``spine-failed.marker``), so a
        dropped drain event is no longer invisible.

        ``trace_id`` correlates a note event with the flush that produced it
        (#188); ``None`` for events outside a traced flush. Callers must keep
        ``data`` small — a drain record shares the spine's PIPE_BUF atomicity
        budget (only bounded metadata is ever passed today).
        """
        if event not in EVENT_VOCAB:
            raise ValueError(f"unknown drain event: {event!r}")
        if self._session_id == SYSTEM_SESSION and event != "transcript-synced":
            raise ValueError(
                f"system drain accepts only 'transcript-synced'; got {event!r}"
            )
        # ponytail: no per-record size cap here; drain producers only ever pass
        # bounded metadata (wiki, wikilink, note path, transcript id). Add a
        # truncate-in-adapter guard if a large-payload producer ever appears.
        SpineWriter(self._lore_root).emit(
            source="drain",
            event=event,
            trace_id=trace_id,
            session_id=self._session_id,
            wiki=wiki,
            data=dict(data),
        )

    def read(
        self,
        *,
        since: datetime | None = None,
        limit: int = 50,
    ) -> list[DrainEvent]:
        """Return up to ``limit`` of this session's drain events from the spine.

        Filtered to ``source="drain"`` and this store's ``session_id``,
        optionally since ``since``. Chronological (oldest first); malformed
        spine lines are skipped by the reader.
        """
        out: list[DrainEvent] = []
        for rec in read_spine(self._lore_root, source="drain"):
            if rec.get("session_id") != self._session_id:
                continue
            ts = _parse_ts(rec.get("ts"))
            if ts is None:
                continue
            if since is not None and ts < since:
                continue
            data = rec.get("data") or {}
            out.append(
                DrainEvent(
                    ts=ts,
                    event=str(rec.get("event", "")),
                    wiki=rec.get("wiki"),
                    session_id=str(rec.get("session_id", "")),
                    data=data,
                    truncated=bool(data.get("truncated", False)),
                )
            )
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

    def read_or_init_cursor(self, *, now: datetime | None = None) -> datetime:
        """Return the existing cursor, or initialise it to ``now`` and return that.

        Cold-start protection for shared drains (chiefly ``_system``):
        a brand-new install or a fresh session has no cursor file, and
        without one the reader would walk back through the entire
        history. Initialising to ``now`` means the first read after
        upgrade surfaces no events; subsequent emits land past the
        cursor and surface normally.
        """
        existing = self.read_cursor()
        if existing is not None:
            return existing
        anchor = now if now is not None else datetime.now(UTC)
        self.write_cursor(anchor)
        return anchor


def _parse_ts(raw: Any) -> datetime | None:
    """Parse a spine ``ts`` (ISO-8601, possibly ``Z``-suffixed)."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


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
