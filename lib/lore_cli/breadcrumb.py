"""Session-start banner rendering — capture state breadcrumb."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from lore_core.ledger import TranscriptLedger, WikiLedger
from lore_core.timefmt import relative_day, relative_time
from lore_core.types import Scope
from lore_core.wiki_config import WikiConfig

# ---------------------------------------------------------------------------
# SessionEnd breadcrumb (file-based buffer, Option B)
# ---------------------------------------------------------------------------

_PENDING_BREADCRUMB_NAME = "pending-breadcrumb.txt"  # legacy; migration only
_PENDING_BREADCRUMB_MAX_AGE_S = 3600  # 1 hour
_EV_WRITTEN = "pending-breadcrumb-written"
_EV_CONSUMED = "pending-breadcrumb-consumed"


def render_session_end_breadcrumb(
    outcome: str,
    pending_after: int,
    threshold: int = 3,
    error_message: str | None = None,
) -> str | None:
    """Return a one-line breadcrumb for a SessionEnd/PreCompact capture result.

    Pure function — no I/O. Returns None for silent outcomes (e.g. no-new-turns).

    outcome values:
      spawned-curator    → "lore: capture queued · curator spawned (pending N)"
      below-threshold    → "lore: capture queued · below threshold (pending N/T)"
      no-new-turns       → None  (silent)
      error              → "lore!: capture error — <message>"
      unattached         → None  (already silent — unattached path is a no-op)
    """
    if outcome == "spawned-curator":
        return f"lore: capture queued · curator spawned (pending {pending_after})"
    if outcome == "below-threshold":
        return f"lore: capture queued · below threshold (pending {pending_after}/{threshold})"
    if outcome == "error":
        msg = error_message or "unknown error"
        return f"lore!: capture error — {msg}"
    # no-new-turns / unattached / anything else → stay silent
    return None


def write_pending_breadcrumb(lore_root: Path, line: str) -> None:
    """Emit a ``pending-breadcrumb-written`` event to hook-events.jsonl.

    Post-Task-9b: storage is a hook-events record, not a standalone file.
    Best-effort; never raises (HookEventLogger swallows OSError internally).
    """
    from lore_core.hook_log import HookEventLogger

    HookEventLogger(lore_root).emit(event=_EV_WRITTEN, line=line)


def consume_pending_breadcrumb(lore_root: Path) -> str | None:
    """Return the most recent unconsumed pending-breadcrumb line.

    Scans ``hook-events.jsonl`` for the most recent written/consumed pair.
    Returns the written line iff it is newer than the last consumed event
    AND younger than ``_PENDING_BREADCRUMB_MAX_AGE_S``. On success, appends
    a ``pending-breadcrumb-consumed`` event so the line is shown at most
    once.

    Also runs the one-shot legacy-file migration: a pre-Task-9b legacy
    ``.lore/pending-breadcrumb.txt`` is read, converted to a written
    event preserving its mtime, and unlinked.
    """
    from datetime import UTC, datetime as _dt
    from lore_core.hook_log import HookEventLogger

    # Migration: convert legacy file to event before scanning.
    migrate_legacy_pending_breadcrumb(lore_root)

    events_path = lore_root / ".lore" / "hook-events.jsonl"
    if not events_path.exists():
        return None

    last_written: dict | None = None
    last_consumed_ts: str | None = None
    try:
        for raw in events_path.read_text().splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            ev = rec.get("event")
            if ev == _EV_WRITTEN:
                last_written = rec
            elif ev == _EV_CONSUMED:
                last_consumed_ts = rec.get("ts")
    except OSError:
        return None

    if last_written is None:
        return None

    written_ts = last_written.get("ts")
    if last_consumed_ts is not None and written_ts is not None and written_ts <= last_consumed_ts:
        return None  # already consumed

    try:
        written_dt = _dt.fromisoformat(str(written_ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if written_dt.tzinfo is None:
        written_dt = written_dt.replace(tzinfo=UTC)
    age = (_dt.now(UTC) - written_dt).total_seconds()
    if age > _PENDING_BREADCRUMB_MAX_AGE_S:
        return None  # stale

    HookEventLogger(lore_root).emit(event=_EV_CONSUMED)
    return last_written.get("line") or None


def migrate_legacy_pending_breadcrumb(lore_root: Path) -> None:
    """One-shot: convert a pre-Task-9b .txt file to a written event + unlink.

    Idempotent — second call is a no-op because the file is unlinked on
    first success. Called from ``consume_pending_breadcrumb`` so users pay
    the migration cost exactly once per vault on the first SessionStart
    after upgrading.
    """
    from datetime import UTC, datetime as _dt
    from lore_core.hook_log import HookEventLogger

    legacy = lore_root / ".lore" / _PENDING_BREADCRUMB_NAME
    if not legacy.exists():
        return
    try:
        line = legacy.read_text().strip()
        mtime = legacy.stat().st_mtime
    except OSError:
        return
    if line:
        ts = _dt.fromtimestamp(mtime, tz=UTC).isoformat().replace("+00:00", "Z")
        # Emit with an explicit ts so staleness uses the file mtime, not now.
        HookEventLogger(lore_root).emit(event=_EV_WRITTEN, line=line, ts=ts)
    try:
        legacy.unlink()
    except OSError:
        pass


@dataclass
class BannerContext:
    """Context for banner rendering."""

    lore_root: Path
    scope: Scope
    wiki_config: WikiConfig
    now: datetime
    note_count: int = 0  # optional — caller may count <wiki>/sessions/*.md


def _most_recent_run_end(lore_root: Path) -> tuple[Path | None, dict | None]:
    """Return (path, run_end_record) or (None, None) if no runs."""
    from lore_core.run_reader import iter_archival_runs
    latest = next(iter(iter_archival_runs(lore_root)), None)
    if latest is None:
        return None, None
    try:
        lines = latest.read_text().splitlines()
    except OSError:
        return None, None
    for line in reversed(lines):
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("type") == "run-end":
            return latest, r
    return latest, None


def _recent_hook_errors(lore_root: Path, *, within: timedelta, now: datetime) -> int:
    """Count hook-events records with outcome=error within the given window."""
    path = lore_root / ".lore" / "hook-events.jsonl"
    if not path.exists():
        return 0
    threshold = now - within
    count = 0
    try:
        for line in path.read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("outcome") != "error":
                continue
            ts_str = r.get("ts")
            if not ts_str:
                continue
            try:
                ts = _parse_ts(ts_str)
            except ValueError:
                continue
            if ts >= threshold:
                count += 1
    except OSError:
        return 0
    return count


def _parse_ts(ts_iso: str) -> datetime:
    ts = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts


def render_banner(ctx: BannerContext, *, errors: list[str] | None = None) -> str | None:
    """Return the banner string, or None if nothing to show (quiet mode + no errors).

    Always single-line. Prefix `lore:` for normal events, `lore!:` for errors.
    Prepends a pending breadcrumb from the last SessionEnd/PreCompact if present.
    """
    mode = ctx.wiki_config.breadcrumb.mode
    errors = errors or []

    # Consume any pending SessionEnd breadcrumb — prepend it regardless of mode.
    session_end_line = consume_pending_breadcrumb(ctx.lore_root)

    # Errors always surface (even in quiet mode).
    if errors:
        banner = "lore!: " + " · ".join(errors)
        if session_end_line:
            return session_end_line + "\n" + banner
        return banner

    if session_end_line and mode == "quiet":
        # Quiet mode: only show the session-end breadcrumb, suppress the rest.
        return session_end_line

    def _prepend(line: str | None, banner: str) -> str:
        """Prepend session_end_line to banner if present."""
        if line:
            return line + "\n" + banner
        return banner

    # Last-run error prefix — preempts all other banners.
    latest_path, run_end = _most_recent_run_end(ctx.lore_root)
    if run_end and run_end.get("errors", 0) > 0:
        short = latest_path.stem.split("-")[-1]
        banner = (
            f"lore!: last run had {run_end['errors']} errors "
            f"({relative_time(_parse_ts(run_end['ts']), now=ctx.now)}) "
            f"· lore runs show {short}"
        )
        return _prepend(session_end_line, banner)

    if mode == "quiet":
        return None

    tledger = TranscriptLedger(ctx.lore_root)
    pending = tledger.pending()
    wledger = WikiLedger(ctx.lore_root, ctx.scope.wiki)
    entry = wledger.read()

    # Lockfile check — curator is running.
    lock_dir = ctx.lore_root / ".lore" / "curator.lock"
    if lock_dir.exists():
        return _prepend(session_end_line, "lore: curator A running in background")

    parts = []
    if pending:
        parts.append(f"{len(pending)} pending")
        if entry.last_curator_a:
            parts.append(f"last curator {relative_time(entry.last_curator_a, now=ctx.now)}")
        if entry.last_briefing:
            parts.append(f"briefing {relative_day(entry.last_briefing, now=ctx.now)}")
        banner = "lore: " + " · ".join(parts)
    else:
        # All-skips hint beats the generic "up to date" when the last run
        # filed nothing (errors=0 already ruled out above).
        if (
            run_end is not None
            and run_end.get("errors", 0) == 0
            and run_end.get("notes_new", 0) == 0
            and run_end.get("notes_merged", 0) == 0
            and run_end.get("skipped", 0) > 0
        ):
            banner = (
                f"lore: last run filed 0 notes "
                f"({run_end['skipped']} skipped) · lore runs show latest"
            )
        else:
            parts.append("up to date")
            parts.append(f"{ctx.note_count} notes in {ctx.scope.wiki}/{ctx.scope.scope}")
            banner = "lore: " + " · ".join(parts)

    # Trailing hook-error segment — non-blocking.
    hook_errors_24h = _recent_hook_errors(
        ctx.lore_root, within=timedelta(hours=24), now=ctx.now
    )
    if hook_errors_24h > 0:
        banner += f" · {hook_errors_24h} hook error{'s' if hook_errors_24h > 1 else ''} today (lore doctor)"
    return _prepend(session_end_line, banner)


