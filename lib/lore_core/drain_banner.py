"""Drain-event rendering for the SessionStart banner.

Two aggregate lines — "this session" and "since you left" — tallied from the
session-scoped and shared ``_system`` drain streams.

Deliberately NOT the same rendering as ``lore status``'s news section, which
lists one line per event under a caller-supplied cutoff. This one tallies,
owns its cursor advance, and cold-starts the system cursor to ``now``. Keep
them separate: the cursor semantics differ (see :func:`render_drain_lines`).
"""

from __future__ import annotations

from collections import Counter
from datetime import timedelta
from pathlib import Path


def render_drain_lines(lore_root: Path, cwd: Path) -> list[str]:
    """Compile the two drain-banner lines shown at SessionStart.

    Line 1 — "· This session"   — session-scoped notes filed/appended
    Line 2 — "· Since you left" — _system events since the system
                                  cursor last advanced

    Both lines are omitted when their respective stream has no new
    events. Returns an empty list when both are silent (callers
    suppress the newline).

    Cursor advance: each stream owns its own cursor. The session
    cursor (``{sid}.cursor``) prevents repeat SessionStarts within
    one Claude run from re-rendering session events. The system
    cursor (``_system.cursor``) is the single authoritative
    "shown through" mark for the shared system stream — without it, a
    stale row on the shared ``_system`` spine stream would haunt every
    fresh session.
    Cold-start initialises ``_system.cursor`` to ``now`` so the first
    read on a new install never reaches back through history.
    """
    from lore_core.drain import SYSTEM_SESSION, DrainStore, resolve_session_id

    sid, _ = resolve_session_id(cwd)
    session_store = DrainStore(lore_root, sid)
    system_store = DrainStore(lore_root, SYSTEM_SESSION)

    session_cursor = session_store.read_cursor()
    session_events = session_store.read(since=session_cursor, limit=200)

    system_cursor = system_store.read_or_init_cursor()
    system_events = system_store.read(since=system_cursor, limit=200)

    lines: list[str] = []
    if session_events:
        counts = tally_drain(session_events)
        summary = format_drain_summary(counts, session_events)
        if summary:
            lines.append(f"  · This session   {summary}")

    if system_events:
        counts = tally_drain(system_events)
        summary = format_drain_summary(counts, system_events)
        if summary:
            lines.append(f"  · Since you left {summary}")

    # Advance each cursor to ``newest + 1µs`` — `since` in DrainStore.read
    # is inclusive (``ts >= since``), so setting the cursor to the event's
    # own ts would resurface it on the next banner call.
    if session_events:
        newest = max(e.ts for e in session_events)
        session_store.write_cursor(newest + timedelta(microseconds=1))
    if system_events:
        newest = max(e.ts for e in system_events)
        system_store.write_cursor(newest + timedelta(microseconds=1))

    return lines


def tally_drain(events) -> dict[str, int]:
    return dict(Counter(e.event for e in events))


def latest_wikilink(events, event_name: str) -> str | None:
    """Return the wikilink from the most recent event of the given type."""
    for e in reversed(events):
        if e.event == event_name:
            return e.data.get("wikilink")
    return None


def format_drain_summary(counts: dict[str, int], events) -> str:
    """Render a short "N notes · M appended · K synced" phrase."""
    parts: list[str] = []
    n_filed = counts.get("note-filed", 0)
    n_appended = counts.get("note-appended", 0)
    n_surface = counts.get("surface-proposed", 0)

    if n_filed:
        wikilink = latest_wikilink(events, "note-filed")
        if wikilink and n_filed == 1:
            parts.append(f"new note {wikilink}")
        else:
            parts.append(f"{n_filed} new notes{wiki_suffix(events, 'note-filed')}")
    if n_appended:
        wikilink = latest_wikilink(events, "note-appended")
        if wikilink and n_appended == 1:
            parts.append(f"added to {wikilink}")
        else:
            parts.append(f"{n_appended} added{wiki_suffix(events, 'note-appended')}")
    if n_surface:
        parts.append(f"{n_surface} surface proposed{wiki_suffix(events, 'surface-proposed')}")
    return " · ".join(parts)


def wiki_suffix(events, event_name: str) -> str:
    """Build ' in <wiki>' or ' (2 in a, 1 in b)' for multi-event tallies.

    Returns "" when any matching event lacks a wiki tag, to avoid
    misleading partial breakdowns on legacy/migration data.
    """
    matching = [e for e in events if e.event == event_name]
    if not matching or any(not e.wiki for e in matching):
        return ""
    tally = Counter(e.wiki for e in matching)
    if len(tally) == 1:
        wiki, _ = next(iter(tally.items()))
        return f" in {wiki}"
    # Highest count first, alphabetical tiebreak — stable across runs.
    items = sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))
    bits = ", ".join(f"{n} in {w}" for w, n in items)
    return f" ({bits})"
