"""Unified event spine — one envelope, one writer, one JSONL family.

All background-pipeline telemetry is emitted through :class:`SpineWriter`
onto ``$LORE_ROOT/.lore/spine.jsonl``. Hook events are the first producer
to migrate (issue #185); curator run events, drain/news events and the
janitor move onto the same envelope in later slices. Any query layer
(``lore status``/``trace``/``doctor``) READS this file, never owns it.

Every record shares the envelope defined here::

    { ts, v, source, event, level, trace_id, session_id, run_id,
      wiki, scope, error_code, data }

* ``source``  — the producer: one of :data:`SOURCES`.
* ``level``   — info / warn / error (:data:`LEVELS`).
* ``error_code`` — ``None`` or a value from the closed :class:`ErrorCode`
  enum. Free-form strings live ONLY inside ``data``; a top-level
  error_code that isn't in the enum is a bug the validator catches.
* Fields a producer can't yet know (``trace_id`` until #188, ``run_id``
  before a run exists, …) are written as explicit ``null`` — never absent,
  so readers never guess between "unknown" and "field dropped".

Schema-version policy (AC5): :data:`SCHEMA_VERSION` is bumped on ANY
change to the envelope *shape* — a field added, removed, renamed, or a
semantic change to an existing field. Adding an :class:`ErrorCode` value
is additive and does NOT bump the version; removing or renaming a code
does. Readers must tolerate an unfamiliar ``v`` by skipping, never
crashing.

Concurrency (preserved verbatim from the audited hook-events writer;
see ``docs/architecture/state.md`` "Concurrency-safety guarantees"):

* **Appends are POSIX-atomic.** ``emit()`` opens the file with
  ``O_APPEND | O_CREAT`` and writes one JSONL record in a single
  ``os.write()``. POSIX guarantees writes ≤ ``PIPE_BUF`` (4096 bytes on
  Linux) to an O_APPEND fd don't interleave between concurrent writers,
  so N sessions append lock-free. Records are kept well under the cap.
* **Rotation is flock-guarded.** ``_maybe_rotate()`` takes a non-blocking
  ``flock`` on a sibling ``spine.rotate.lock``; losers skip rotation this
  cycle (the next emit retries) so two writers can't both ``os.replace``.
* **Failures are observable.** Any ``OSError`` in ``emit()`` touches
  ``spine-failed.marker`` so ``lore doctor`` / ``lore status`` surface
  "spine writes are failing" without crashing the hot path.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

# Bump on any envelope-shape change (see module docstring).
SCHEMA_VERSION = 1

# Closed producer set. A source outside this set is a bug, not data.
SOURCES: frozenset[str] = frozenset(
    {"hook", "curator", "drain", "janitor", "install", "mcp", "flag"}
)

LEVELS: frozenset[str] = frozenset({"info", "warn", "error"})

# Envelope field order — the exact key set every record carries.
ENVELOPE_FIELDS: tuple[str, ...] = (
    "ts",
    "v",
    "source",
    "event",
    "level",
    "trace_id",
    "session_id",
    "run_id",
    "wiki",
    "scope",
    "error_code",
    "data",
)


class ErrorCode(StrEnum):
    """Closed enum of structured error codes.

    Adding a value is additive (no version bump). Free-form detail —
    exception type, message, offending path — belongs in ``data``, not
    here. str-subclass so ``json.dumps`` serialises the value directly.
    """

    # Hook producer (issue #185).
    CAPTURE_FAILED = "capture-failed"
    UNKNOWN_INTEGRATION = "unknown-integration"
    FLUSH_REQUEST_FAILED = "flush-request-failed"
    FLUSH_HANDOVER_TIMEOUT = "flush-handover-timeout"
    SPAWN_RUNAWAY = "spawn-runaway"
    SPAWN_STAMP_MIGRATION_FAILED = "spawn-stamp-migration-failed"
    LEDGER_WRITE_FAILED = "ledger-write-failed"
    # The spine failing to write itself — surfaced via the degrade marker,
    # never emitted onto the (unwritable) spine.
    SPINE_WRITE_FAILED = "spine-write-failed"
    # Flush lifecycle state machine (issue #189). Dead-letter reasons —
    # the flush pipeline's formerly-silent failure paths, now structured.
    COMPOSE_FAILED = "compose-failed"
    SPAWN_FAILED = "spawn-failed"
    SIDECAR_READ_FAILED = "sidecar-read-failed"
    CHAPTER_APPEND_FAILED = "chapter-append-failed"


_ERROR_CODE_VALUES: frozenset[str] = frozenset(c.value for c in ErrorCode)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def new_trace_id() -> str:
    """Mint a correlation id for one flush.

    Threaded hook decision point -> detached-flush spawn (env var) -> curator
    run events -> drain event -> published note, so a whole flush is
    correlatable and concurrent flushes stay separable. Opaque and short;
    readers only ever compare it for equality.
    """
    return secrets.token_hex(8)


def _oldest_record_age_days(path: Path) -> float | None:
    """Age in days of the first record's ``ts``, or None if unreadable.

    Only the first line is read — cheap enough for the janitor's
    opportunistic per-hook check even on a multi-MB spine file.
    """
    try:
        with path.open("r") as f:
            first = f.readline()
    except OSError:
        return None
    first = first.strip()
    if not first:
        return None
    try:
        rec = json.loads(first)
        ts = datetime.fromisoformat(str(rec["ts"]).replace("Z", "+00:00"))
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return (datetime.now(UTC) - ts).total_seconds() / 86400


class SpineWriter:
    """Single-record appender for the event spine. Never raises.

    I/O-free at construction — no file is opened until :meth:`emit`.
    """

    def __init__(self, lore_root: Path, *, max_size_mb: int = 10) -> None:
        self._dir = lore_root / ".lore"
        self._path = self._dir / "spine.jsonl"
        self._rotated = self._dir / "spine.jsonl.1"
        self._rotate_lock = self._dir / "spine.rotate.lock"
        self._marker = self._dir / "spine-failed.marker"
        self._max_size = max_size_mb * 1024 * 1024

    def emit(
        self,
        *,
        source: str,
        event: str,
        level: str = "info",
        trace_id: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        wiki: str | None = None,
        scope: str | None = None,
        error_code: ErrorCode | str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Append one envelope record. Best-effort; swallows OSError."""
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._maybe_rotate()
            payload = {
                "ts": _now_iso(),
                "v": SCHEMA_VERSION,
                "source": source,
                "event": event,
                "level": level,
                "trace_id": trace_id,
                "session_id": session_id,
                "run_id": run_id,
                "wiki": wiki,
                "scope": scope,
                "error_code": error_code.value if isinstance(error_code, ErrorCode) else error_code,
                "data": data or {},
            }
            line = (json.dumps(payload, default=str) + "\n").encode()
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
        self._rotate_locked(due=lambda: self._over_size(self._max_size))

    def janitor_rotate_if_due(self, *, max_age_days: float, max_size_mb: float) -> bool:
        """Force a hot->cold downgrade if age or the CURRENT config's size
        cap is exceeded. Returns True iff a rotation happened.

        Used by the retention janitor (#190), distinct from the size-only
        trigger baked into ``emit()`` at construction time: re-checking
        against live config here means a user who tightens
        ``observability.hook_events.max_size_mb`` gets it enforced on the
        next opportunistic pass, and a low-traffic spine that never trips
        the size cap still ages out of the hot tier.
        """
        max_bytes = max_size_mb * 1024 * 1024

        def _due() -> bool:
            if self._over_size(max_bytes):
                return True
            oldest = _oldest_record_age_days(self._path)
            return oldest is not None and oldest > max_age_days

        if not self._path.exists() or not _due():
            return False
        return self._rotate_locked(due=_due)

    def _over_size(self, max_bytes: float) -> bool:
        try:
            return self._path.stat().st_size >= max_bytes
        except OSError:
            return False

    def _rotate_locked(self, *, due) -> bool:
        """Rename hot -> cold under the rotation flock. Returns True iff
        this call performed the rotation.

        ``due`` is re-evaluated *after* the lock is held — a TOCTOU guard:
        the caller's outer check can go stale between "size looked over
        cap" and "lock acquired" if another writer rotated (and a fresh,
        small file was recreated) in between. A contended/lost race also
        yields False — the loser skips, matching ``emit()``'s semantics.
        """
        from lore_core.lockfile import flocked

        try:
            with flocked(self._rotate_lock, blocking=False) as held:
                if not held or not self._path.exists() or not due():
                    return False
                os.replace(self._path, self._rotated)
                return True
        except OSError:
            return False

    def _touch_marker(self) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._marker.touch(exist_ok=True)
            os.utime(self._marker, None)
        except OSError:
            pass


def validate_envelope(rec: dict[str, Any]) -> None:
    """Raise ``ValueError`` if ``rec`` is not a well-formed envelope.

    Used by tests (AC1) and any strict reader. Checks exact field set,
    the closed source/level/error_code sets, and null-or-string typing
    of the correlation fields.
    """
    if not isinstance(rec, dict):
        raise ValueError(f"envelope must be a dict, got {type(rec).__name__}")
    keys = set(rec)
    if keys != set(ENVELOPE_FIELDS):
        missing = set(ENVELOPE_FIELDS) - keys
        extra = keys - set(ENVELOPE_FIELDS)
        raise ValueError(f"envelope field mismatch: missing={missing}, extra={extra}")
    if not isinstance(rec["ts"], str) or not rec["ts"]:
        raise ValueError("ts must be a non-empty string")
    if not isinstance(rec["v"], int) or isinstance(rec["v"], bool) or rec["v"] < 1:
        raise ValueError(f"v must be a positive int, got {rec['v']!r}")
    if rec["source"] not in SOURCES:
        raise ValueError(f"source {rec['source']!r} not in {sorted(SOURCES)}")
    if not isinstance(rec["event"], str) or not rec["event"]:
        raise ValueError("event must be a non-empty string")
    if rec["level"] not in LEVELS:
        raise ValueError(f"level {rec['level']!r} not in {sorted(LEVELS)}")
    if rec["error_code"] is not None and rec["error_code"] not in _ERROR_CODE_VALUES:
        raise ValueError(
            f"error_code {rec['error_code']!r} not in closed enum "
            f"(free-form strings belong in data)"
        )
    for f in ("trace_id", "session_id", "run_id", "wiki", "scope"):
        if rec[f] is not None and not isinstance(rec[f], str):
            raise ValueError(f"{f} must be str or null, got {type(rec[f]).__name__}")
    if not isinstance(rec["data"], dict):
        raise ValueError("data must be a dict")


def read_spine(lore_root: Path, *, source: str | None = None) -> list[dict[str, Any]]:
    """Return spine records (live file only), optionally filtered by ``source``.

    Malformed lines are skipped (an interrupted final write can only be a
    single unparseable line). Reads ``spine.jsonl`` only — parity with the
    prior hook-events readers, which never walked the rotated sibling.
    """
    path = lore_root / ".lore" / "spine.jsonl"
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        for raw in path.read_text().splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            if source is not None and rec.get("source") != source:
                continue
            out.append(rec)
    except OSError:
        return []
    return out


# ---------------------------------------------------------------------------
# Hook producer adapter
#
# The seam every hook-domain caller (hooks, breadcrumb, spawn, ledger) uses
# to reach the spine. It maps the legacy flat-kwargs shape — a rich
# ``outcome`` string plus arbitrary detail kwargs — onto the envelope:
# outcome and extras go into ``data``; ``level`` is derived from ``outcome``;
# a ``{"wiki","scope"}`` payload is split into the two envelope fields.
# ---------------------------------------------------------------------------

# outcome values that mean "degraded but not a hard error".
_WARN_OUTCOMES: frozenset[str] = frozenset({"warning", "prior-runaway", "flush-handover-timeout"})


def _hook_level(outcome: str | None) -> str:
    if outcome == "error":
        return "error"
    if outcome in _WARN_OUTCOMES:
        return "warn"
    return "info"


def emit_hook_event(
    lore_root: Path,
    *,
    event: str,
    outcome: str | None = None,
    level: str | None = None,
    wiki: str | None = None,
    scope: dict[str, Any] | str | None = None,
    run_id: str | None = None,
    trace_id: str | None = None,
    session_id: str | None = None,
    error: Any = None,
    error_code: ErrorCode | str | None = None,
    **data: Any,
) -> None:
    """Emit a hook-source event onto the spine. Never raises.

    ``scope`` accepts the legacy ``{"wiki": ..., "scope": ...}`` payload
    (split into the envelope's ``wiki``/``scope``) or a plain scope string.
    ``outcome``, ``error`` and any surplus kwargs land in ``data``.
    """
    if isinstance(scope, dict):
        wiki = wiki if wiki is not None else scope.get("wiki")
        scope = scope.get("scope")
    payload: dict[str, Any] = dict(data)
    if outcome is not None:
        payload["outcome"] = outcome
    if error is not None:
        payload["error"] = error
    SpineWriter(lore_root).emit(
        source="hook",
        event=event,
        level=level if level is not None else _hook_level(outcome),
        trace_id=trace_id,
        session_id=session_id,
        run_id=run_id,
        wiki=wiki,
        scope=scope,
        error_code=error_code,
        data=payload,
    )
