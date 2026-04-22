"""Relative-time formatting.

One canonical ``relative_time()`` with consistent tz handling and
bucket labels for every caller that needs "how long ago".

**Future-timestamp policy (pinned):** a timestamp in the future (clock
skew, not-yet-happened event) renders as ``"just now"`` rather than a
negative-delta string. This is deliberate: the primary use is "how
stale is this?" — clock skew yielding a negative value is noise, not
signal. Do not change this without updating every caller.

- ``relative_time(ts)`` — seconds-granular: "just now" / "5m ago" /
  "2h ago" / "3d ago" / "2w ago". ``short=True`` drops " ago" and
  renders "just now" as "now".
- ``relative_day(ts)`` — day-granular: "today" / "yesterday" / "3d ago",
  falling back to ``relative_time()`` for anything older than a week.
"""

from __future__ import annotations

from datetime import UTC, datetime


def _parse(ts: datetime | str | None) -> datetime | str | None:
    """Coerce str → datetime (UTC-aware). Returns original input if unparseable.

    A bare ``str`` that cannot be parsed is returned as-is so the caller's
    caller sees something recognisable in the UI (rather than ``"?"`` which
    hides the input).
    """
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return ts  # return the original string
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def relative_time(
    ts: datetime | str | None,
    *,
    now: datetime | None = None,
    short: bool = False,
) -> str:
    """Render a timestamp as "X ago" or "just now".

    Returns ``"?"`` for None/empty input. Returns the original string
    unchanged if parsing fails (so unexpected input surfaces in the UI
    rather than silently becoming ``"?"``).

    Future timestamps (ts > now) render as "just now" — see the module
    docstring for rationale.
    """
    parsed = _parse(ts)
    if parsed is None:
        return "?"
    if isinstance(parsed, str):
        return parsed  # unparseable — pass through

    if now is None:
        now = datetime.now(UTC)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    seconds = (now - parsed).total_seconds()

    # Future / clock-skew: pin to "just now".
    if seconds < 0:
        return "now" if short else "just now"

    if seconds < 60:
        return "now" if short else "just now"
    if seconds < 3600:
        m = int(seconds // 60)
        return f"{m}m" if short else f"{m}m ago"
    if seconds < 86400:
        h = int(seconds // 3600)
        return f"{h}h" if short else f"{h}h ago"
    d = int(seconds // 86400)
    if d == 1 and not short:
        return "yesterday"  # user-facing copy; short mode keeps numeric "1d"
    if d < 7:
        return f"{d}d" if short else f"{d}d ago"
    w = d // 7
    return f"{w}w" if short else f"{w}w ago"


def relative_day(
    ts: datetime | str | None,
    *,
    now: datetime | None = None,
) -> str:
    """Day-granular rendering: "today" / "yesterday" / "3d ago" / falls
    back to :func:`relative_time` for anything ≥7 days.

    Use for briefing/digest contexts where calendar-day semantics matter
    more than hours-since (e.g., "last briefing today" reads better than
    "last briefing 14h ago" when it was this morning).
    """
    parsed = _parse(ts)
    if parsed is None:
        return "?"
    if isinstance(parsed, str):
        return parsed

    if now is None:
        now = datetime.now(UTC)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    delta_days = (now.date() - parsed.date()).days
    if delta_days <= 0:
        return "today"
    if delta_days == 1:
        return "yesterday"
    if delta_days < 7:
        return f"{delta_days}d ago"
    return relative_time(parsed, now=now)
