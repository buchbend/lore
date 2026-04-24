"""Run-log writer for curator invocations (A, B, C).

Two output files per run:
  - runs/<id>.jsonl            archival
  - runs-live.jsonl            tee of active run (truncated at run-start)

Plus an optional LLM-trace companion runs/<id>.trace.jsonl when
LORE_TRACE_LLM=1 or --trace-llm is set.
"""

from __future__ import annotations

import json
import os
import secrets
import string
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any


_ID_ALPHABET = string.ascii_lowercase + string.digits  # 36 chars


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


class RunLogger:
    """Write a curator run's decision trace.

    Context-manager usage:

        with RunLogger(lore_root, trigger="hook", role="b") as logger:
            logger.emit("cluster-formed", surface_names=[...], note_count=3)
            ...

    Opens `runs/<id>.jsonl` and truncates `runs-live.jsonl` at start;
    emits run-start. On exit (normal or exception) emits run-end
    with duration and counts, then closes files.

    Writes are best-effort: OSError during emit increments
    `_write_failures` and is swallowed.
    """

    RECORD_TYPES = frozenset({
        "run-start", "run-end", "skip", "warning", "error",
        "llm-prompt", "llm-response",
        # Curator A
        "transcript-start", "redaction", "noteworthy",
        "merge-check", "session-note",
        # Curator B
        "cluster-formed", "surface-filed",
        # Curator C
        "action-applied", "action-skipped", "defrag-pass",
        "wiki-start", "wiki-skip",
    })

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
    ):
        self._lore_root = lore_root
        self._dir = lore_root / ".lore"
        self._runs_dir = self._dir / "runs"
        self._trigger = trigger
        self._role = role
        self._pending_count = pending_count
        self._config_snapshot = config_snapshot or {}
        self._dry_run = dry_run
        self._trace_llm = trace_llm
        self._ledger_snapshot_hash = ledger_snapshot_hash
        self.run_id = run_id or generate_run_id()
        self._archival = self._runs_dir / f"{self.run_id}.jsonl"
        self._trace = self._runs_dir / f"{self.run_id}.trace.jsonl"
        self._live = self._dir / "runs-live.jsonl"
        self._write_failures = 0
        self._counts = {
            "notes_new": 0, "notes_merged": 0, "skipped": 0, "errors": 0,
            "clusters_formed": 0, "surfaces_emitted": 0,
            "actions_applied": 0, "actions_skipped": 0,
        }
        self._opened_at: datetime | None = None

    @property
    def trace_enabled(self) -> bool:
        return self._trace_llm

    def __enter__(self) -> "RunLogger":
        # Best-effort setup — NEVER raise. Observability must not break
        # the pipeline. If mkdir or collision-retry fails, subsequent
        # emit()s will no-op on the missing/locked file and surface via
        # _write_failures in run-end.
        try:
            self._runs_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            self._write_failures += 1

        # Init invariant — suffix collision guard. If BOTH attempts land
        # on existing files, fall back to a failure-marked state rather
        # than raising.
        try:
            if self._archival.exists():
                self.run_id = generate_run_id()
                self._archival = self._runs_dir / f"{self.run_id}.jsonl"
                self._trace = self._runs_dir / f"{self.run_id}.trace.jsonl"
                if self._archival.exists():
                    # Extremely unlikely (clock frozen + 36^6 collision).
                    # Count as a write failure, set paths to /dev/null so
                    # emit() no-ops safely.
                    self._write_failures += 1
                    self._archival = Path(os.devnull)
                    self._trace = Path(os.devnull)
        except OSError:
            self._write_failures += 1

        # Truncate live-tee.
        try:
            self._live.parent.mkdir(parents=True, exist_ok=True)
            self._live.write_text("")
        except OSError:
            self._write_failures += 1

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
            self.emit(
                "error",
                exception=type(exc).__name__,
                message=exc_message,
            )
        duration_ms = 0
        if self._opened_at is not None:
            duration_ms = int((datetime.now(UTC) - self._opened_at).total_seconds() * 1000)
        self.emit(
            "run-end",
            duration_ms=duration_ms,
            role=self._role,
            **self._counts,
            dry_run=self._dry_run,
            log_write_failures=self._write_failures,
        )
        # Lazy retention — best-effort, must not raise.
        try:
            from lore_core.run_retention import enforce_retention
            from lore_core.root_config import load_root_config
            cfg = load_root_config(self._lore_root).observability.runs
            enforce_retention(
                self._lore_root,
                keep=cfg.keep,
                max_total_mb=cfg.max_total_mb,
                keep_trace=cfg.keep_trace,
            )
        except Exception:
            pass

    def emit(self, record_type: str, **fields: Any) -> None:
        """Emit one decision record. Never raises."""
        if record_type not in self.RECORD_TYPES:
            fields = {"unknown_type": record_type, **fields}
            record_type = "warning"
        payload = {
            **fields,
            "type": record_type,
            "schema_version": 1,
            "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        self._counters_bookkeeping(record_type, fields)
        self._write(self._archival, payload, mode="a")
        self._write(self._live, {"run_id": self.run_id, **payload}, mode="a")
        if self._trace_llm and record_type in ("llm-prompt", "llm-response"):
            self._write(self._trace, payload, mode="a")

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
        elif record_type == "cluster-formed":
            self._counts["clusters_formed"] += 1
        elif record_type == "surface-filed":
            self._counts["surfaces_emitted"] += 1
        elif record_type == "action-applied":
            self._counts["actions_applied"] += 1
        elif record_type == "action-skipped":
            self._counts["actions_skipped"] += 1

    def _write(self, path: Path, payload: dict[str, Any], *, mode: str) -> None:
        try:
            encoded = json.dumps(payload, default=str) + "\n"
        except (TypeError, ValueError):
            self._write_failures += 1
            return
        try:
            with path.open(mode) as f:
                f.write(encoded)
        except OSError:
            self._write_failures += 1
