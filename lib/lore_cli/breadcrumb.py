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
    if outcome == "error":
        msg = error_message or "unknown error"
        return f"lore!: capture error — {msg}"
    return None


def write_pending_breadcrumb(lore_root: Path, line: str) -> None:
    """Emit a ``pending-breadcrumb-written`` event to hook-events.jsonl.

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

    Also runs the one-shot legacy-file migration: a legacy
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
    """One-shot: convert a legacy .txt file to a written event + unlink.

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


def render_banner(ctx: BannerContext, *, errors: list[str] | None = None) -> str | None:
    """Return the banner string, or None if nothing to show (quiet mode + no errors).

    Always single-line. Prefix ``lore:`` for normal events, ``lore!:`` for
    errors. Prepends a pending breadcrumb from the last SessionEnd/PreCompact
    if present.

    Reads from ``query_capture_state`` — all liveness fields flow
    through CaptureState, no direct file reads in this function.
    """
    from lore_core.capture_state import query_capture_state

    mode = ctx.wiki_config.breadcrumb.mode
    errors = errors or []

    session_end_line = consume_pending_breadcrumb(ctx.lore_root)

    if errors:
        banner = "lore!: " + " · ".join(errors)
        if session_end_line:
            return session_end_line + "\n" + banner
        return banner

    if session_end_line and mode == "quiet":
        return session_end_line

    def _prepend(line: str | None, banner: str) -> str:
        if line:
            return line + "\n" + banner
        return banner

    state = query_capture_state(ctx.lore_root, now=ctx.now)
    a = next((c for c in state.curators if c.role == "a"), None)

    # Last-run error prefix — preempts everything else (banner's error mode).
    if a and a.last_run_errors and a.last_run_errors > 0 and a.last_run_ts and a.last_run_short_id:
        banner = (
            f"lore!: last run had {a.last_run_errors} errors "
            f"({relative_time(a.last_run_ts, now=ctx.now)}) "
            f"· lore runs show {a.last_run_short_id}"
        )
        return _prepend(session_end_line, banner)

    if mode == "quiet":
        return session_end_line

    # Hook errors are operational alerts — always surface.
    if state.hook_errors_24h > 0:
        suffix = "s" if state.hook_errors_24h > 1 else ""
        banner = f"lore!: {state.hook_errors_24h} hook error{suffix} today (lore doctor)"
        return _prepend(session_end_line, banner)

    return session_end_line


