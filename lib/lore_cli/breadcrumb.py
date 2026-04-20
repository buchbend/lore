"""Session-start banner rendering — capture state breadcrumb."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from lore_core.ledger import TranscriptLedger, WikiLedger
from lore_core.types import Scope
from lore_core.wiki_config import WikiConfig

# ---------------------------------------------------------------------------
# SessionEnd breadcrumb (file-based buffer, Option B)
# ---------------------------------------------------------------------------

_PENDING_BREADCRUMB_NAME = "pending-breadcrumb.txt"
_PENDING_BREADCRUMB_MAX_AGE_S = 3600  # 1 hour


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
    """Write *line* to .lore/pending-breadcrumb.txt (best-effort, never raises)."""
    try:
        dest = lore_root / ".lore" / _PENDING_BREADCRUMB_NAME
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(line)
    except OSError:
        pass


def consume_pending_breadcrumb(lore_root: Path) -> str | None:
    """Read and delete the pending breadcrumb file if it exists and is fresh.

    Returns the stored line (stripped), or None if absent / stale / unreadable.
    Deletes the file after reading so it is shown at most once.
    """
    import time as _time

    dest = lore_root / ".lore" / _PENDING_BREADCRUMB_NAME
    if not dest.exists():
        return None
    try:
        age = _time.time() - dest.stat().st_mtime
        if age > _PENDING_BREADCRUMB_MAX_AGE_S:
            dest.unlink(missing_ok=True)
            return None
        line = dest.read_text().strip()
        dest.unlink(missing_ok=True)
        return line or None
    except OSError:
        return None


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
    runs_dir = lore_root / ".lore" / "runs"
    if not runs_dir.exists():
        return None, None
    files = sorted(
        (p for p in runs_dir.glob("*.jsonl") if not p.name.endswith(".trace.jsonl")),
        key=lambda p: p.name,
    )
    if not files:
        return None, None
    latest = files[-1]
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
            f"({_relative_time(_parse_ts(run_end['ts']), ctx.now)}) "
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
            parts.append(f"last curator {_relative_time(entry.last_curator_a, ctx.now)}")
        if entry.last_briefing:
            parts.append(f"briefing {_relative_time_short(entry.last_briefing, ctx.now)}")
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


def _relative_time(ts: datetime, now: datetime) -> str:
    """'2h ago' | '3d ago' | 'yesterday' | 'just now' — short form."""
    # Ensure tz-aware subtraction
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    delta = now - ts
    seconds = delta.total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        m = int(seconds // 60)
        return f"{m}m ago"
    if seconds < 86400:
        h = int(seconds // 3600)
        return f"{h}h ago"
    d = int(seconds // 86400)
    if d == 1:
        return "yesterday"
    if d < 7:
        return f"{d}d ago"
    return f"{d // 7}w ago"


def _relative_time_short(ts: datetime, now: datetime) -> str:
    """'yesterday' | 'today' | '3d ago' — for briefing."""
    if hasattr(ts, "date") and hasattr(now, "date"):
        delta_days = (now.date() - ts.date()).days
        if delta_days == 0:
            return "today"
        if delta_days == 1:
            return "yesterday"
        if delta_days < 7:
            return f"{delta_days}d ago"
    return _relative_time(ts, now)
