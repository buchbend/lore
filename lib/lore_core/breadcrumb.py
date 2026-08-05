"""Session-start banner rendering — capture state breadcrumb."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from lore_core.timefmt import relative_time
from lore_core.types import Scope
from lore_core.wiki_config import WikiConfig

# ---------------------------------------------------------------------------
# SessionEnd breadcrumb (file-based buffer, Option B)
# ---------------------------------------------------------------------------

_PENDING_BREADCRUMB_MAX_AGE_S = 3600  # 1 hour
_EV_WRITTEN = "pending-breadcrumb-written"
_EV_CONSUMED = "pending-breadcrumb-consumed"


def render_session_end_breadcrumb(
    outcome: str,
    pending_after: int,
    error_message: str | None = None,
) -> str | None:
    """Return a one-line breadcrumb for a SessionEnd/PreCompact capture result.

    Pure function — no I/O. Returns None for silent outcomes (e.g. no-new-turns).

    Only an error is worth a line. Capture is silent on success: the
    transcript is registered and nothing further is pending on the user.

    outcome values:
      captured           → None  (silent)
      no-new-turns       → None  (silent)
      error              → "lore!: capture error — <message>"
      unattached         → None  (already silent — unattached path is a no-op)
    """
    if outcome == "error":
        msg = error_message or "unknown error"
        return f"lore!: capture error — {msg}"
    return None


def write_pending_breadcrumb(lore_root: Path, line: str) -> None:
    """Emit a ``pending-breadcrumb-written`` event onto the event spine.

    Best-effort; never raises (the spine writer swallows OSError internally).
    """
    from lore_core.spine import emit_hook_event

    emit_hook_event(lore_root, event=_EV_WRITTEN, line=line)


def consume_pending_breadcrumb(lore_root: Path) -> str | None:
    """Return the most recent unconsumed pending-breadcrumb line.

    Scans the event spine for the most recent written/consumed pair.
    Returns the written line iff it is newer than the last consumed event
    AND younger than ``_PENDING_BREADCRUMB_MAX_AGE_S``. On success, appends
    a ``pending-breadcrumb-consumed`` event so the line is shown at most
    once.
    """
    from datetime import UTC
    from datetime import datetime as _dt

    from lore_core.spine import emit_hook_event, read_spine

    last_written: dict | None = None
    last_consumed_ts: str | None = None
    for rec in read_spine(lore_root, source="hook"):
        ev = rec.get("event")
        if ev == _EV_WRITTEN:
            last_written = rec
        elif ev == _EV_CONSUMED:
            last_consumed_ts = rec.get("ts")

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

    emit_hook_event(lore_root, event=_EV_CONSUMED)
    return (last_written.get("data") or {}).get("line") or None


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
            "· lore trace last"
        )
        return _prepend(session_end_line, banner)

    if mode == "quiet":
        return session_end_line

    # Hook errors are operational alerts — always surface.
    if state.hook_errors_24h > 0:
        suffix = "s" if state.hook_errors_24h > 1 else ""
        banner = f"lore!: {state.hook_errors_24h} hook error{suffix} today (lore doctor)"
        return _prepend(session_end_line, banner)

    scope_warning = _scope_drift_warning(ctx)
    if scope_warning:
        return _prepend(session_end_line, scope_warning)

    return session_end_line


def _scope_drift_warning(ctx: BannerContext) -> str | None:
    """Warn when this repo's checked-in ``.lore.yml`` still declares a
    scope the registry no longer has this attachment under.

    ``lore scopes rename`` rewrites vault-local state only — it never
    edits a checked-in offer file (that file may live on a different
    host entirely). Left uncorrected, a future ``lore attach accept``
    re-derives the stale scope from the file and resurrects it. This
    mirrors the fingerprint-DRIFT notice SessionStart already prints
    for content changes, but catches the case fingerprint-matching
    can't: the file's content hasn't changed, only the registry's
    idea of this attachment's scope has (via rename).
    """
    from lore_core.offer import FILENAME, parse_lore_yml

    repo_root = ctx.scope.claude_md_path.parent
    offer = parse_lore_yml(repo_root / FILENAME)
    if offer is None or offer.scope == ctx.scope.scope:
        return None
    return (
        f"lore: {repo_root / FILENAME} still declares scope `{offer.scope}`, "
        f"but this repo is registered under `{ctx.scope.scope}` (renamed?). "
        f"Update the file's `scope:` field, then run "
        f"`lore attach accept --cwd {repo_root}` to resync."
    )


