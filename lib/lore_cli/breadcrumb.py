"""Session-start banner rendering — capture state breadcrumb."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from lore_core.ledger import TranscriptLedger, WikiLedger
from lore_core.types import Scope
from lore_core.wiki_config import WikiConfig


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

    Always single-line. Prefix `lore:` for normal events, `lore!:` for errors.
    """
    mode = ctx.wiki_config.breadcrumb.mode
    errors = errors or []

    # Errors always surface (even in quiet mode).
    if errors:
        return "lore!: " + " · ".join(errors)

    if mode == "quiet":
        return None

    tledger = TranscriptLedger(ctx.lore_root)
    pending = tledger.pending()
    wledger = WikiLedger(ctx.lore_root, ctx.scope.wiki)
    entry = wledger.read()

    # Lockfile check — curator is running.
    lock_dir = ctx.lore_root / ".lore" / "curator.lock"
    if lock_dir.exists():
        return "lore: curator A running in background"

    parts = []
    if pending:
        parts.append(f"{len(pending)} pending")
        if entry.last_curator_a:
            parts.append(f"last curator {_relative_time(entry.last_curator_a, ctx.now)}")
        if entry.last_briefing:
            parts.append(f"briefing {_relative_time_short(entry.last_briefing, ctx.now)}")
        return "lore: " + " · ".join(parts)

    # Up-to-date
    parts.append("up to date")
    parts.append(f"{ctx.note_count} notes in {ctx.scope.wiki}/{ctx.scope.scope}")
    return "lore: " + " · ".join(parts)


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
