"""Persisted flush lifecycle state machine (issue #189).

Replaces the old "retry forever, or silently give up with no marker" flush
behaviour with an explicit, queryable record. One record per flush unit —
keyed by the buffer stem — tracks a flush through::

    queued -> running -> published | withheld | dead-lettered(reason)

with an attempt counter and a next-eligible-retry timestamp. A stuck flush
is now a row you can ``list``, not an absence of evidence.

The record is the source of truth for "what is in-flight right now"; the
event *history* lives on the spine — every transition emits an envelope
(``source="curator"``, ``event="flush-<state>"``), so a record reopened for a
new unit never erases the trail. Slices #192 (``lore trace``) and #193
(``lore status``) read this store, so :meth:`FlushStore.list` is a clean
queryable API, not an internal detail.

Persistence mirrors the sibling buffer store: one small JSON file per record
under ``.lore/flushes/`` mutated under a per-record ``flocked`` lock. No
daemon, no index — JSONL/JSON plus in-command aggregation is sufficient at
these volumes (PRD 0005, "Out of scope").
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from lore_core.lockfile import flocked
from lore_core.spine import ErrorCode, SpineWriter

SCHEMA_VERSION = 1

# Bounded-retry policy. attempts counts failed runs; the MAX_ATTEMPTS-th
# failure dead-letters instead of scheduling another retry.
MAX_ATTEMPTS = 3
RETRY_BASE_SECONDS = 60
RETRY_CAP_SECONDS = 3600  # backoff ceiling — a stuck buffer retries hourly, not never


class FlushState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PUBLISHED = "published"
    WITHHELD = "withheld"
    DEAD_LETTERED = "dead-lettered"


_TERMINAL: frozenset[FlushState] = frozenset(
    {FlushState.PUBLISHED, FlushState.WITHHELD, FlushState.DEAD_LETTERED}
)

# The legal transition table — the heart of the machine. ``running -> queued``
# is a *scheduled retry*; terminal states have no outgoing edges. Self-loops
# are deliberately absent: an idempotent caller re-reads via ``begin`` rather
# than re-transitioning.
_LEGAL: dict[FlushState, frozenset[FlushState]] = {
    FlushState.QUEUED: frozenset({FlushState.RUNNING}),
    FlushState.RUNNING: frozenset(
        {
            FlushState.PUBLISHED,
            FlushState.WITHHELD,
            FlushState.DEAD_LETTERED,
            FlushState.QUEUED,
        }
    ),
    FlushState.PUBLISHED: frozenset(),
    FlushState.WITHHELD: frozenset(),
    FlushState.DEAD_LETTERED: frozenset(),
}


class FlushTransitionError(RuntimeError):
    """Raised when a transition is not in :data:`_LEGAL`."""

    def __init__(self, frm: FlushState, to: FlushState) -> None:
        super().__init__(f"illegal flush transition: {frm.value} -> {to.value}")
        self.frm = frm
        self.to = to


def is_legal_transition(frm: FlushState | str, to: FlushState | str) -> bool:
    """Pure predicate over the transition table (AC1)."""
    return FlushState(to) in _LEGAL[FlushState(frm)]


def backoff_seconds(attempt: int) -> int:
    """Exponential backoff, capped. ``attempt`` is 1-based (first retry == 1)."""
    if attempt < 1:
        return 0
    return min(RETRY_BASE_SECONDS * (2 ** (attempt - 1)), RETRY_CAP_SECONDS)


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _parse_iso(s: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


@dataclass
class FlushRecord:
    """One flush unit's persisted state. ``flush_id == buffer_stem``."""

    flush_id: str
    buffer_stem: str
    state: str = FlushState.QUEUED.value
    attempts: int = 0
    next_retry_at: str | None = None  # ISO-Z; set only while a retry is scheduled
    reason: str | None = None  # ErrorCode value on dead-letter; else None
    wiki: str = ""
    trace_id: str | None = None  # #188 stamps this
    created_at: str = ""
    updated_at: str = ""
    schema_version: int = SCHEMA_VERSION

    @property
    def is_terminal(self) -> bool:
        return FlushState(self.state) in _TERMINAL

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> FlushRecord:
        known = {f: d[f] for f in cls.__dataclass_fields__ if f in d}
        return cls(**known)


@dataclass
class PurgeResult:
    """Outcome of one :meth:`FlushStore.purge` pass."""

    deleted: int = 0
    failed: int = 0


def is_retry_due(rec: FlushRecord, *, now: datetime | None = None) -> bool:
    """True when a queued record's backoff has elapsed (retry may proceed)."""
    if FlushState(rec.state) is not FlushState.QUEUED:
        return False
    if not rec.next_retry_at:
        return True
    due = _parse_iso(rec.next_retry_at)
    if due is None:
        return True
    return (now or _now()) >= due


class FlushStore:
    """Queryable per-flush record store. Mutations serialise per-record.

    Persistence and the spine emit are best-effort in the sense the spine
    itself never raises; the JSON write does raise on a genuinely broken FS
    (the caller is already inside the flush pipeline's own error handling).
    """

    def __init__(self, lore_root: Path) -> None:
        self._lore_root = Path(lore_root)
        self._dir = self._lore_root / ".lore" / "flushes"

    # -- paths -------------------------------------------------------------
    def _path(self, flush_id: str) -> Path:
        return self._dir / f"{flush_id}.json"

    def _lock_path(self, flush_id: str) -> Path:
        return self._dir / f"{flush_id}.lock"

    # -- read --------------------------------------------------------------
    def get(self, flush_id: str) -> FlushRecord | None:
        path = self._path(flush_id)
        try:
            return FlushRecord.from_dict(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError, TypeError):
            return None

    def list(self, *, state: FlushState | str | None = None) -> list[FlushRecord]:
        """All records, optionally filtered by state. Newest-updated first."""
        if not self._dir.exists():
            return []
        want = FlushState(state).value if state is not None else None
        out: list[FlushRecord] = []
        for p in self._dir.glob("*.json"):
            try:
                rec = FlushRecord.from_dict(json.loads(p.read_text()))
            except (OSError, json.JSONDecodeError, TypeError):
                continue
            if want is None or rec.state == want:
                out.append(rec)
        out.sort(key=lambda r: r.updated_at, reverse=True)
        return out

    # -- write -------------------------------------------------------------
    def _persist(self, rec: FlushRecord) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        rec.updated_at = _iso(_now())
        self._path(rec.flush_id).write_text(json.dumps(rec.to_dict(), default=str))

    def _emit(self, rec: FlushRecord, *, level: str) -> None:
        SpineWriter(self._lore_root).emit(
            source="curator",
            event=f"flush-{rec.state}",
            level=level,
            trace_id=rec.trace_id,
            wiki=rec.wiki or None,
            error_code=rec.reason,  # ErrorCode value only on dead-letter, else None
            data={
                "flush_id": rec.flush_id,
                "buffer_stem": rec.buffer_stem,
                "attempts": rec.attempts,
                "next_retry_at": rec.next_retry_at,
            },
        )

    def begin(
        self, buffer_stem: str, *, wiki: str = "", trace_id: str | None = None
    ) -> FlushRecord:
        """Return the active record for this buffer, or open a fresh queued unit.

        Idempotent while a unit is in flight (attempts preserved across retry
        calls); a terminal record is reopened as a new queued unit so one
        buffer's successive flushes each get their own lifecycle.
        """
        flush_id = buffer_stem
        with flocked(self._lock_path(flush_id)):
            existing = self.get(flush_id)
            if existing is not None and not existing.is_terminal:
                return existing
            now = _iso(_now())
            rec = FlushRecord(
                flush_id=flush_id,
                buffer_stem=buffer_stem,
                state=FlushState.QUEUED.value,
                wiki=wiki or (existing.wiki if existing else ""),
                trace_id=trace_id,
                created_at=now,
                updated_at=now,
            )
            self._persist(rec)
        self._emit(rec, level="info")
        return rec

    def transition(
        self,
        rec: FlushRecord,
        to: FlushState | str,
        *,
        reason: ErrorCode | None = None,
    ) -> FlushRecord:
        """Move ``rec`` to ``to`` if the table allows it; persist and emit.

        ``reason`` is required to be an :class:`ErrorCode` (never a free-form
        string) and is only meaningful on a dead-letter transition.
        """
        to = FlushState(to)
        frm = FlushState(rec.state)
        if to not in _LEGAL[frm]:
            raise FlushTransitionError(frm, to)
        if reason is not None and not isinstance(reason, ErrorCode):
            raise ValueError(f"reason must be an ErrorCode, got {reason!r}")
        with flocked(self._lock_path(rec.flush_id)):
            rec.state = to.value
            if to is FlushState.DEAD_LETTERED and reason is not None:
                rec.reason = reason.value
            if to is not FlushState.QUEUED:
                rec.next_retry_at = None
            self._persist(rec)
        level = "error" if to is FlushState.DEAD_LETTERED else "info"
        self._emit(rec, level=level)
        return rec

    # -- retention (issue #190) ---------------------------------------------
    def purge(self, *, terminal_max_age_days: float, dead_letter_hard_cap: int) -> PurgeResult:
        """Delete resolved records past their window; cap unresolved ones.

        PUBLISHED/WITHHELD are resolved — deleted once older than
        ``terminal_max_age_days`` (by ``updated_at``). DEAD_LETTERED is
        exempt from the age window (a human still needs to act on it) but
        capped by count: beyond ``dead_letter_hard_cap`` the oldest are
        dropped so a permanently-stuck pipeline can't grow the store
        forever. QUEUED/RUNNING (in-flight) are never touched here — that's
        the flush pipeline's job, not retention's.
        """
        result = PurgeResult()
        writer = SpineWriter(self._lore_root)
        now = _now()

        terminal_ok = (FlushState.PUBLISHED, FlushState.WITHHELD)
        for rec in self.list():
            state = FlushState(rec.state)
            if state not in terminal_ok:
                continue
            updated = _parse_iso(rec.updated_at)
            age_days = (now - updated).total_seconds() / 86400 if updated else 0
            if age_days > terminal_max_age_days:
                self._purge_one(rec, writer=writer, result=result)

        dead = sorted(self.list(state=FlushState.DEAD_LETTERED), key=lambda r: r.updated_at)
        overflow = len(dead) - dead_letter_hard_cap
        for rec in dead[: max(overflow, 0)]:
            self._purge_one(rec, writer=writer, result=result)

        return result

    def _purge_one(self, rec: FlushRecord, *, writer: SpineWriter, result: PurgeResult) -> None:
        path = self._path(rec.flush_id)
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        with flocked(self._lock_path(rec.flush_id)):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                result.failed += 1
                writer.emit(
                    source="janitor",
                    event="retention-delete-failed",
                    level="warn",
                    trace_id=rec.trace_id,
                    wiki=rec.wiki or None,
                    data={"family": "flush-record", "flush_id": rec.flush_id, "error": str(exc)},
                )
                return
        result.deleted += 1
        writer.emit(
            source="janitor",
            event="retention-delete",
            trace_id=rec.trace_id,
            wiki=rec.wiki or None,
            data={
                "family": "flush-record",
                "flush_id": rec.flush_id,
                "bytes": size,
                "state": rec.state,
            },
        )

    def record_failure(self, rec: FlushRecord, *, error_code: ErrorCode) -> FlushRecord:
        """Register a failed run: schedule a backed-off retry, or dead-letter.

        The caller is expected to be in ``running``. The ``MAX_ATTEMPTS``-th
        failure dead-letters with ``error_code``; earlier failures re-queue
        with an exponential backoff written into ``next_retry_at``.
        """
        rec.attempts += 1
        if rec.attempts >= MAX_ATTEMPTS:
            return self.transition(rec, FlushState.DEAD_LETTERED, reason=error_code)
        # transition() persists the whole record, carrying the bumped
        # attempt count and the backoff we set here.
        rec.next_retry_at = _iso(_now() + timedelta(seconds=backoff_seconds(rec.attempts)))
        return self.transition(rec, FlushState.QUEUED)
