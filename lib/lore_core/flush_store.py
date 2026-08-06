"""Leftover flush records — read and retention only.

The flush lifecycle state machine (issue #189) tracked a flush through
``queued -> running -> published | withheld | dead-lettered``. Issue #361
deleted the compose pipeline, which was the machine's only driver, and
issue #377 deleted the write half that survived it. What remains is a
reader over the records already on disk, plus the retention pass that
clears them.

No code opens a new record. :meth:`FlushStore.purge` is the one live
caller's entry point (the retention janitor, #190): resolved records age
out, dead-lettered ones are capped by count, and the store empties itself
over one retention horizon.

Persistence is unchanged: one small JSON file per record under
``.lore/flushes/``, mutated under a per-record ``flocked`` lock.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from lore_core.lockfile import flocked
from lore_core.spine import SpineWriter
from lore_core.timefmt import parse_ts

SCHEMA_VERSION = 1


class FlushState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PUBLISHED = "published"
    WITHHELD = "withheld"
    DEAD_LETTERED = "dead-lettered"


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


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


class FlushStore:
    """Reader and retention pass over the records already on disk."""

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

    # -- retention (issue #190) ---------------------------------------------
    def purge(self, *, terminal_max_age_days: float, dead_letter_hard_cap: int) -> PurgeResult:
        """Delete resolved records past their window; cap unresolved ones.

        PUBLISHED/WITHHELD are resolved — deleted once older than
        ``terminal_max_age_days`` (by ``updated_at``). DEAD_LETTERED is
        exempt from the age window (a human still needs to act on it) but
        capped by count: beyond ``dead_letter_hard_cap`` the oldest are
        dropped so a permanently-stuck pipeline can't grow the store
        forever. QUEUED/RUNNING records are left alone here; no code opens
        one any more, so the survivors are pre-#361 leftovers a human
        clears by deleting ``.lore/flushes/``.
        """
        result = PurgeResult()
        writer = SpineWriter(self._lore_root)
        now = _now()

        terminal_ok = (FlushState.PUBLISHED, FlushState.WITHHELD)
        for rec in self.list():
            state = FlushState(rec.state)
            if state not in terminal_ok:
                continue
            updated = parse_ts(rec.updated_at)
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
