"""Unified retention janitor (issue #190).

One flock-guarded sweep enforces tiered retention across the event spine
and the flush record store, replacing the old inconsistent per-family
behavior (10 MB hook rotation, count+MB run caps with swallowed delete
errors, append-forever drain, soft-hidden crash logs). Runs
opportunistically from hook fire / curator run end (see
``lore_cli._janitor_entry``) — no daemon.

Tiers, per spine file:

* **hot** — the live ``spine.jsonl``: detailed events, short window
  (default 7 days). An age or (currently-configured) size overage
  triggers a downgrade: rotate hot -> cold (reuses
  :meth:`SpineWriter.janitor_rotate_if_due`, itself sharing the rotation
  flock with the size-triggered rotation ``emit()`` does inline).
* **cold** — the rotated ``spine.jsonl.1``: the retained window before
  deletion (default 30 days) with an independent size cap. No tier below
  cold — an overage deletes the file outright.

.. note::
    This is file-level tiering, not per-record compaction: a "downgrade"
    moves the whole file, it doesn't strip fields to shrink individual
    records. Compacting cold-tier records into denser summaries is a
    plausible future slice; nothing here depends on it.

Legacy run-archival files and crash logs are separate families with their
own age/count windows (see :mod:`lore_core.run_retention` and
``lore_cli._crash_log``); this module composes run-archival + the flush
store's dead-letter/terminal purge under ONE lock since both live under
``lore_root/.lore/``. Crash-log purge and drain-orphan pruning are
independently safe under light concurrency (atomic replace / tolerant of
FileNotFoundError) so they're composed alongside this, not inside the same
critical section — see ``lore_cli._janitor_entry.run_opportunistic_janitor``.

Every deletion and tier-downgrade emits a ``source="janitor"`` spine event;
a delete failure emits a warn-level event instead of failing silently.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from lore_core.flush_store import FlushStore
from lore_core.lockfile import flocked
from lore_core.root_config import ObservabilityConfig
from lore_core.run_retention import enforce_retention
from lore_core.spine import SpineWriter


@dataclass
class JanitorReport:
    """Outcome of one :func:`run_janitor` pass."""

    ran: bool = True  # False iff the lock was contended
    hot_bytes: int = 0
    cold_bytes: int = 0
    deleted: int = 0
    downgraded: bool = False
    failed: int = 0


@contextmanager
def janitor_lock(lore_root: Path):
    """Non-blocking sibling-flock guarding one janitor pass at a time.

    Yields ``True`` iff acquired. A contended lock means another sweep is
    already running — the caller skips this cycle; the next opportunistic
    trigger retries. No stale-lock recovery needed: flock releases on
    process exit (see ``lore_core.lockfile.flocked``).
    """
    with flocked(lore_root / ".lore" / "janitor.lock", blocking=False) as held:
        yield held


def run_janitor(lore_root: Path, cfg: ObservabilityConfig) -> JanitorReport:
    """Enforce tiered retention across the spine + flush store. Never raises.

    Self-contained critical section: acquires :func:`janitor_lock`, sweeps
    the hot/cold spine tiers, the legacy run-archival family, and the
    flush store's terminal/dead-letter purge, then persists the queryable
    usage snapshot (see :func:`read_janitor_status`).
    """
    with janitor_lock(lore_root) as held:
        if not held:
            return JanitorReport(ran=False)

        report = JanitorReport()
        writer = SpineWriter(lore_root)
        lore_dir = lore_root / ".lore"
        hot = lore_dir / "spine.jsonl"
        cold = lore_dir / "spine.jsonl.1"

        # Hot -> cold: age or the currently-configured size cap.
        if SpineWriter(lore_root).janitor_rotate_if_due(
            max_age_days=cfg.retention.hot_days,
            max_size_mb=cfg.hook_events.max_size_mb,
        ):
            report.downgraded = True

        # Cold tier: age or size cap deletes it outright — no tier below.
        if cold.exists():
            _maybe_delete_cold(cold, cfg, writer=writer, report=report)

        # Legacy run-archival files (pre-migration; RunLogger no longer
        # writes here — see run_log.py). Already emits its own events.
        enforce_retention(
            lore_root,
            keep=cfg.runs.keep,
            max_total_mb=cfg.runs.max_total_mb,
            keep_trace=cfg.runs.keep_trace,
        )

        # Flush store: terminal records age out, dead letters survive
        # unless over the hard cap. Already emits its own events.
        purge_result = FlushStore(lore_root).purge(
            terminal_max_age_days=cfg.retention.cold_days,
            dead_letter_hard_cap=cfg.retention.dead_letter_hard_cap,
        )
        report.deleted += purge_result.deleted
        report.failed += purge_result.failed

        report.hot_bytes = _size_or_zero(hot)
        report.cold_bytes = _size_or_zero(cold)
        _write_status(lore_root, report)
        return report


def _maybe_delete_cold(
    cold: Path, cfg: ObservabilityConfig, *, writer: SpineWriter, report: JanitorReport
) -> None:
    try:
        size = cold.stat().st_size
        age_days = (time.time() - cold.stat().st_mtime) / 86400
    except OSError:
        return
    over_age = age_days > cfg.retention.cold_days
    over_size = size > cfg.retention.cold_max_mb * 1024 * 1024
    if not (over_age or over_size):
        return
    try:
        cold.unlink()
        report.deleted += 1
        writer.emit(
            source="janitor",
            event="retention-delete",
            data={"family": "spine-cold", "path": cold.name, "bytes": size},
        )
    except FileNotFoundError:
        pass
    except OSError as exc:
        report.failed += 1
        writer.emit(
            source="janitor",
            event="retention-delete-failed",
            level="warn",
            data={"family": "spine-cold", "path": cold.name, "error": str(exc)},
        )


def _size_or_zero(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _write_status(lore_root: Path, report: JanitorReport) -> None:
    status_path = lore_root / ".lore" / "janitor-status.json"
    payload = {
        "last_run_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "hot_bytes": report.hot_bytes,
        "cold_bytes": report.cold_bytes,
        "deleted": report.deleted,
        "failed": report.failed,
    }
    try:
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(json.dumps(payload))
    except OSError:
        pass


def read_janitor_status(lore_root: Path) -> dict | None:
    """Pure read of the last janitor pass's usage snapshot.

    Returns None when the janitor has never run or the status file is
    missing/corrupt — callers (``lore status``, #193) treat that as "no
    data yet", not an error.
    """
    status_path = lore_root / ".lore" / "janitor-status.json"
    try:
        return json.loads(status_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
