"""Pre/post-diff audit log for Curator C runs.

One file per day at ``$LORE_ROOT/.lore/curator-c.diff.YYYY-MM-DD.log``.
Each entry carries ``run_id`` so the diff can be cross-referenced with
the structured ``runs/<id>.jsonl`` log. Zero-change runs compress to a
single-line marker so the audit log stays grep-clean.

Retention: 90 days (older files pruned on the next run).
Rotation: 10 MB per log → rotates to ``.log.1``.
Failures: permission denied → emits a ``curator-c/diff-log-write-failed``
event to ``hook-events.jsonl`` (observable via CaptureState), never raises.
"""

from __future__ import annotations

import difflib
import os
import time
from datetime import UTC, datetime
from pathlib import Path


_ROTATE_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_RETENTION_DAYS_DEFAULT = 90


def _log_path(lore_root: Path, ts: datetime | None = None) -> Path:
    stamp = (ts or datetime.now(UTC)).strftime("%Y-%m-%d")
    return lore_root / ".lore" / f"curator-c.diff.{stamp}.log"


def _maybe_rotate(log: Path) -> None:
    """If the log is ≥ 10 MB, move it to ``<log>.1`` (overwrites prior .1)."""
    try:
        size = log.stat().st_size
    except OSError:
        return
    if size < _ROTATE_MAX_BYTES:
        return
    rotated = log.with_suffix(log.suffix + ".1")
    try:
        os.replace(log, rotated)
    except OSError:
        pass


def _render_unified_diff(
    before: dict[str, str], after: dict[str, str]
) -> list[str]:
    """Return unified-diff blocks (one per changed file) as strings."""
    blocks: list[str] = []
    all_files = sorted(set(before) | set(after))
    for f in all_files:
        b = before.get(f, "")
        a = after.get(f, "")
        if a == b:
            continue
        diff_lines = list(
            difflib.unified_diff(
                b.splitlines(keepends=True),
                a.splitlines(keepends=True),
                fromfile=f"a/{f}",
                tofile=f"b/{f}",
                lineterm="",
            )
        )
        if diff_lines:
            blocks.append("\n".join(diff_lines))
    return blocks


def _render_summary_table(summary: dict[str, int]) -> str:
    if not summary:
        return "(no summary counts)"
    rows = [f"  {k}: {v}" for k, v in sorted(summary.items()) if v]
    return "\n".join(rows) if rows else "(all counts zero)"


def _emit_warning_event(lore_root: Path, outcome: str, message: str) -> None:
    """Emit a curator-c warning event; never raise."""
    try:
        from lore_core.hook_log import HookEventLogger
        HookEventLogger(lore_root).emit(
            event="curator-c", outcome=outcome, error={"message": message}
        )
    except Exception:
        pass


def write_diff_log_entry(
    lore_root: Path,
    *,
    run_id: str,
    snapshot_before: dict[str, str],
    snapshot_after: dict[str, str],
    dry_run: bool,
    summary: dict[str, int],
    now: datetime | None = None,
) -> None:
    """Append one entry to today's log. Never raises on I/O failure.

    Zero-change runs (snapshots equal + empty summary) write a single-line
    marker and skip the full entry structure.
    """
    now = now or datetime.now(UTC)
    log = _log_path(lore_root, now)
    log.parent.mkdir(parents=True, exist_ok=True)

    _maybe_rotate(log)

    try:
        diff_blocks = _render_unified_diff(snapshot_before, snapshot_after)
        summary_has_data = any(v for v in summary.values())
        is_noop = not diff_blocks and not summary_has_data

        if is_noop:
            line = f"{now.isoformat().replace('+00:00', 'Z')} run={run_id} status=no-op\n"
            with log.open("a") as f:
                f.write(line)
            return

        mode = "DRY-RUN" if dry_run else "apply"
        header = (
            f"═══ run={run_id} ts={now.isoformat().replace('+00:00', 'Z')} "
            f"mode={mode} ═══"
        )
        parts = [header, "Summary:", _render_summary_table(summary), ""]
        if diff_blocks:
            parts.append("Diffs:")
            parts.extend(diff_blocks)
        parts.append("")  # trailing blank
        with log.open("a") as f:
            f.write("\n".join(parts) + "\n")
    except OSError as exc:
        _emit_warning_event(
            lore_root, "diff-log-write-failed", f"{type(exc).__name__}: {exc}"
        )


def prune_old_diff_logs(
    lore_root: Path, *, retention_days: int = _RETENTION_DAYS_DEFAULT
) -> None:
    """Delete diff logs older than ``retention_days`` days. Never raises."""
    lore_dir = lore_root / ".lore"
    if not lore_dir.exists():
        return
    threshold = time.time() - retention_days * 86400
    for p in lore_dir.glob("curator-c.diff.*.log*"):
        try:
            if p.stat().st_mtime < threshold:
                p.unlink()
        except OSError:
            continue
