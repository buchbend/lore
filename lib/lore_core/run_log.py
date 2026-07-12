"""Run-log producer for curator invocations (A, B, C).

Curator run events are emitted onto the unified event spine
(``source="curator"``) — one envelope per decision record, keyed by
``run_id``. There is no longer a per-run archival file or a ``runs-live``
tee; ``lore trace`` / ``lore runs`` reconstruct a run by grouping spine
records on ``run_id`` (see :mod:`lore_core.run_reader`).

The :class:`RunLogger` context-manager API is unchanged, so every producer
(``session_curator``, ``hygiene``, ``curator_cmd``) is untouched — only the
sink moved. Two consequences of the move:

* **LLM records carry metadata only.** The spine's lock-free O_APPEND
  atomicity assumes each record stays well under ``PIPE_BUF`` (4 KB); a full
  prompt/response would blow past that and risk interleaved writes. So
  ``llm-prompt`` / ``llm-response`` keep ``call``/``tier``/``token_count`` and
  drop the message bodies. Full-text LLM tracing is no longer persisted.
* **Retention is not enforced here.** The spine self-rotates and the unified
  janitor (#190) owns tiered retention; ``RunLogger`` no longer runs cleanup.
"""

from __future__ import annotations

import secrets
import string
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any

from lore_core.spine import SpineWriter

_ID_ALPHABET = string.ascii_lowercase + string.digits  # 36 chars

# llm records keep only these small metadata fields on the spine; the full
# prompt / response text is intentionally dropped (see module docstring).
_LLM_META_FIELDS: tuple[str, ...] = ("call", "tier", "token_count", "model", "latency_ms")


def generate_run_id(*, now: datetime | None = None) -> str:
    """Return `<ISO-timestamp>-<6-char-random-suffix>` for a run.

    Timestamp is filename-safe (hyphens, no colons). Suffix is 6
    chars from [a-z0-9] — collisions inside the retention window
    are astronomically unlikely.
    """
    ts = now or datetime.now(UTC)
    stamp = ts.strftime("%Y-%m-%dT%H-%M-%S")
    suffix = "".join(secrets.choice(_ID_ALPHABET) for _ in range(6))
    return f"{stamp}-{suffix}"


RecordCallback = Callable[[str, dict[str, Any]], None]  # (record_type, full_payload)


class RunLogger:
    """Emit a curator run's decision trace onto the event spine.

    Context-manager usage:

        with RunLogger(lore_root, trigger="hook", role="a") as logger:
            logger.emit("session-note", action="filed", path="...")
            ...

    Emits run-start on enter and run-end (with duration + counts) on exit;
    an exception in the body emits an ``error`` record and then propagates.
    """

    RECORD_TYPES = frozenset(
        {
            "run-start",
            "run-end",
            "skip",
            "warning",
            "error",
            "llm-prompt",
            "llm-response",
            # Curator A
            "transcript-start",
            "redaction",
            "noteworthy",
            "cascade-verdict",  # shadow-run feature-based classifier
            "merge-check",
            "session-note",
            # Buffer-and-flush curator (plan: very-good-thats-the-mossy-lobster).
            # Lifecycle: opened -> appended* -> (cap-tripped|requested) -> spawned
            #   -> deterministic-completed -> llm-completed | degraded
            # Reaper / handover events are siblings.
            "buffer-opened",
            "buffer-appended",
            "buffer-cap-tripped",
            "flush-requested",
            "flush-spawned",
            "flush-deterministic-completed",
            "flush-llm-completed",
            "flush-degraded",
            "flush-handover-timeout",
            "reaper-scanned",
            "reaper-force-flushed",
            "dangling-ref",
            # Hygiene pass (lore curator [--wiki] [--apply])
            "action-applied",
            "action-skipped",
            "wiki-start",
        }
    )

    def __init__(
        self,
        lore_root: Path,
        *,
        trigger: str = "hook",
        role: str = "a",
        pending_count: int = 0,
        config_snapshot: dict[str, Any] | None = None,
        dry_run: bool = False,
        trace_llm: bool = False,
        ledger_snapshot_hash: str | None = None,
        run_id: str | None = None,
        trace_id: str | None = None,
        on_record: RecordCallback | None = None,
    ):
        self._lore_root = lore_root
        self._writer = SpineWriter(lore_root)
        self._trigger = trigger
        self._role = role
        self._pending_count = pending_count
        self._config_snapshot = config_snapshot or {}
        self._dry_run = dry_run
        self._trace_llm = trace_llm
        self._ledger_snapshot_hash = ledger_snapshot_hash
        self.run_id = run_id or generate_run_id()
        # One correlation id for the whole flush; stamped on every emit so a
        # run's events join the drain event and the published note (#188).
        self.trace_id = trace_id
        self._counts = {
            "notes_new": 0,
            "notes_merged": 0,
            "skipped": 0,
            "errors": 0,
            "actions_applied": 0,
            "actions_skipped": 0,
        }
        self._on_record = on_record
        self._opened_at: datetime | None = None

    @property
    def trace_enabled(self) -> bool:
        return self._trace_llm

    def __enter__(self) -> RunLogger:
        self._opened_at = datetime.now(UTC)
        self.emit(
            "run-start",
            run_id=self.run_id,
            role=self._role,
            trigger=self._trigger,
            pending_count=self._pending_count,
            config=self._config_snapshot,
            dry_run=self._dry_run,
            ledger_snapshot_hash=self._ledger_snapshot_hash,
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc is not None:
            try:
                exc_message = str(exc)
            except Exception:
                exc_message = "<exception __str__ raised>"
            self.emit("error", exception=type(exc).__name__, message=exc_message)
        duration_ms = 0
        if self._opened_at is not None:
            duration_ms = int((datetime.now(UTC) - self._opened_at).total_seconds() * 1000)
        self.emit(
            "run-end",
            duration_ms=duration_ms,
            role=self._role,
            **self._counts,
            dry_run=self._dry_run,
        )
        # Retention is not enforced here: the spine self-rotates and the
        # unified janitor (#190) owns tiered retention.

    def emit(self, record_type: str, **fields: Any) -> None:
        """Emit one decision record onto the spine. Never raises."""
        if record_type not in self.RECORD_TYPES:
            fields = {"unknown_type": record_type, **fields}
            record_type = "warning"
        ts = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        payload = {**fields, "type": record_type, "schema_version": 1, "ts": ts}
        self._counters_bookkeeping(record_type, fields)
        if self._on_record is not None:
            try:
                self._on_record(record_type, payload)
            except Exception:
                pass
        self._writer.emit(
            source="curator",
            event=record_type,
            level=self._level(record_type),
            run_id=self.run_id,
            trace_id=self.trace_id,
            wiki=fields.get("wiki"),
            scope=fields.get("scope") if isinstance(fields.get("scope"), str) else None,
            data=self._spine_data(record_type, fields),
        )

    @staticmethod
    def _level(record_type: str) -> str:
        # "error" and "warning" are the producer's explicit error/warn channels;
        # everything else is informational.
        if record_type == "error":
            return "error"
        if record_type == "warning":
            return "warn"
        return "info"

    @staticmethod
    def _spine_data(record_type: str, fields: dict[str, Any]) -> dict[str, Any]:
        if record_type in ("llm-prompt", "llm-response"):
            # Metadata only — the full prompt/response text is dropped.
            return {k: fields[k] for k in _LLM_META_FIELDS if k in fields}
        return dict(fields)

    def _counters_bookkeeping(self, record_type: str, fields: dict[str, Any]) -> None:
        if record_type == "session-note":
            action = fields.get("action")
            if action == "filed":
                self._counts["notes_new"] += 1
            elif action == "merged":
                self._counts["notes_merged"] += 1
        elif record_type == "skip":
            self._counts["skipped"] += 1
        elif record_type == "error":
            self._counts["errors"] += 1
        elif record_type == "action-applied":
            self._counts["actions_applied"] += 1
        elif record_type == "action-skipped":
            self._counts["actions_skipped"] += 1
