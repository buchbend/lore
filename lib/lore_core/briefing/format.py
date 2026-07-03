"""Deterministic briefing formatter.

Turns the output of :func:`lore_core.briefing.gather` into a markdown
digest without any LLM step. Used by the top-level ``lore briefing``
one-shot command for daily publish-and-forget; the LLM-prose path lives
in the ``/lore:briefing`` skill (which still goes through
``gather`` + a separate ``publish`` call).

Shape:

    # Briefing — <today> · <wiki>

    <N> session(s) since <last_briefing or "the start">.

    ## <YYYY-MM-DD>
    - **<slug>** — <summary>
    - **<slug>** — <summary>

Per-session ``<summary>`` resolution order:
    1. ``frontmatter.summary`` (curator-written)
    2. ``frontmatter.description``
    3. ``""`` (slug alone)

The new note shape has no H2 structure to mine a fallback bullet from —
a session's full body (disclaimer + chapters of topic blocks) is
available via ``session["body"]`` for the LLM composer, but this
deterministic fallback formatter stays frontmatter-only.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def _session_summary(session: dict[str, Any]) -> str:
    fm = session.get("frontmatter") or {}
    summary = fm.get("summary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()
    description = fm.get("description")
    if isinstance(description, str) and description.strip():
        return description.strip()
    return ""


def render_briefing(gather_result: dict[str, Any]) -> str:
    """Render a deterministic briefing markdown from gather output.

    Used as the fallback when the LLM composer is unavailable or fails.
    Returns ``""`` when there are no new sessions — the caller decides
    whether to skip publishing or send an "all quiet" note.
    """
    sessions = gather_result.get("new_sessions") or []
    if not sessions:
        return ""

    wiki = gather_result.get("wiki", "")
    today = gather_result.get("today", "")
    last = (gather_result.get("ledger") or {}).get("last_briefing")
    since = last or "the start"

    lines: list[str] = []
    header = f"# Briefing — {today}" + (f" · {wiki}" if wiki else "")
    lines.append(header)
    lines.append("")
    count = len(sessions)
    plural = "session" if count == 1 else "sessions"
    lines.append(f"{count} {plural} since {since}.")
    lines.append("")

    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in sessions:
        by_date[s.get("date", "")].append(s)

    for d in sorted(by_date, reverse=True):
        lines.append(f"## {d}")
        lines.append("")
        for s in by_date[d]:
            slug = s.get("slug") or "(unknown)"
            summary = _session_summary(s)
            if summary:
                lines.append(f"- **{slug}** — {summary}")
            else:
                lines.append(f"- **{slug}**")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
