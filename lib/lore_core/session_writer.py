"""Session-note body primitives — parse, render, merge.

PR 6b of the streamlining track (issue #80) trimmed this module down to
the body-section primitives shared by the buffer-and-flush curator
(``lore_curator.stub_note``, ``lore_curator.synthesis``), the explicit
``/lore:session`` path (``lore_core.session``), and the linter
(``lore_core.lint``). The legacy classify-per-chunk ``SessionInput`` +
``file_or_merge`` surface was deleted along with
``lore_curator.summary_merge`` and the ``LORE_BUFFER_FLUSH`` toggle.

Layout invariant
----------------

Every session note still lives at::

    <wiki>/sessions[/<handle>]/<YYYY>/<MM>/<DD>-<HHMM>-<slug>.md

``<handle>`` is present iff the wiki is in team mode (``_users.yml``
exists — see ``lore_core.identity.session_note_dir``). The shared
``session_path_sort_key`` below understands both the modern
``DD-HHMM-slug.md`` shape and the legacy ``DD-slug.md`` shape.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple


# ---------------------------------------------------------------------------
# Body section primitives — Phase 2 of the session-note revision
#
# Session-note bodies are rendered as a fixed set of sections:
#
#   # <title>
#   ## Summary           ← 4-5 sentence narrative paragraph
#   ## Decisions made    ← bullets (rationale-bearing)
#   ## What we worked on ← bullets (activity narrative)
#   ## Activity          ← parent for mechanical extracts (Phase 3 populates)
#     ### Commits
#     ### Issues opened
#     ### Issues closed
#   ## Loose ends        ← bullets (past-tense / stative)
#
# The parser below is permissive — unrecognised content is dropped
# (legacy ``### Files touched`` / ``Entities:`` lines fade out as old
# notes are touched), but recognised section content round-trips
# losslessly.
# ---------------------------------------------------------------------------


class BodySections(NamedTuple):
    """Structured view of a session-note body for parse → merge → render."""

    title: str
    summary: str               # paragraph (multiline allowed; no leading "- ")
    adr_candidates: list[str]  # rendered bullet lines (top-level + sub-bullets)
    worked_on: list[str]
    loose_ends: list[str]
    commits: list[str]         # under ### Commits
    issues_opened: list[str]
    issues_closed: list[str]
    # discussion-shape companion to ``worked_on``: narrative bullets of
    # what was talked through when no edits happened. Conditional —
    # rendered only when non-empty (the renderer omits empty sections).
    # Plan ``yes-do-that-keen-yeti`` step-5. Defaults to ``()`` (an
    # immutable empty sentinel) at the NamedTuple level so existing
    # constructor sites stay positional-compatible without re-typing
    # an empty list literal at each call.
    discussion: list[str] = ()  # type: ignore[assignment]
    # P2 narrative — a single markdown string carrying bold-led bullets
    # with ``@N`` turn citations (and optional sub-headings). When set,
    # the renderer emits ``## Narrative`` between Summary and Activity.
    # P2 (experiment 005, GPT-OSS-120B hero cell) replaces the structured
    # ``worked_on`` / ``discussion`` / ``adr_candidates`` / ``loose_ends``
    # sections with this single field; those fields stay on BodySections
    # for legacy-note round-trip but are not populated by new notes.
    narrative: str = ""


_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$")
_KNOWN_H2: dict[str, str] = {
    "Summary": "summary",
    "Narrative": "narrative",
    # New heading (slice #63) — canonical.
    "ADR candidates": "adr_candidates",
    # Legacy alias — maps to the same field; renderer always emits the new heading.
    "Decisions made": "adr_candidates",
    "Discussion": "discussion",
    "What we worked on": "worked_on",
    "Activity": "activity",
    "Loose ends": "loose_ends",
}

_ADR_GLOSS = (
    "_ADR = Architecture Decision Record. Proposals worth promoting later; "
    "most sessions have none._"
)
_KNOWN_H3_UNDER_ACTIVITY: dict[str, str] = {
    "Commits": "commits",
    "Issues opened": "issues_opened",
    "Issues closed": "issues_closed",
}


def parse_body_sections(body: str) -> BodySections:
    """Split a session-note body into its locked sections.

    Recognises the H1 title, the five locked H2 sections, and the three
    H3 subheadings under ``## Activity``. Any other content (legacy
    ``### Files touched``, freeform ``Entities:`` lines, free-form text
    inside Decisions/What-we-worked-on) is silently dropped — old shape
    content fades out the next time a note is appended to.

    The summary is the only section that keeps non-bullet text; bullet
    sections only retain lines that start with ``-``.
    """
    title = ""
    summary_lines: list[str] = []
    narrative_lines: list[str] = []
    adr_candidates: list[str] = []
    discussion: list[str] = []
    worked_on: list[str] = []
    loose_ends: list[str] = []
    commits: list[str] = []
    issues_opened: list[str] = []
    issues_closed: list[str] = []
    current_h2: str | None = None
    current_h3_in_activity: str | None = None

    for raw_line in body.splitlines():
        line = raw_line.rstrip()
        m = _HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            heading = m.group(2)
            if level == 1:
                title = heading
                current_h2 = None
                current_h3_in_activity = None
            elif level == 2:
                current_h2 = _KNOWN_H2.get(heading)
                current_h3_in_activity = None
            elif level == 3 and current_h2 == "activity":
                current_h3_in_activity = _KNOWN_H3_UNDER_ACTIVITY.get(heading)
            continue

        if current_h2 == "summary":
            summary_lines.append(line)
        elif current_h2 == "narrative":
            narrative_lines.append(line)
        elif current_h2 == "adr_candidates" and line.lstrip().startswith("-"):
            adr_candidates.append(line)
        elif current_h2 == "discussion" and line.lstrip().startswith("-"):
            discussion.append(line)
        elif current_h2 == "worked_on" and line.lstrip().startswith("-"):
            worked_on.append(line)
        elif current_h2 == "loose_ends" and line.lstrip().startswith("-"):
            loose_ends.append(line)
        elif current_h2 == "activity" and current_h3_in_activity is not None \
                and line.lstrip().startswith("-"):
            if current_h3_in_activity == "commits":
                commits.append(line)
            elif current_h3_in_activity == "issues_opened":
                issues_opened.append(line)
            elif current_h3_in_activity == "issues_closed":
                issues_closed.append(line)

    return BodySections(
        title=title,
        summary="\n".join(summary_lines).strip(),
        adr_candidates=adr_candidates,
        worked_on=worked_on,
        loose_ends=loose_ends,
        commits=commits,
        issues_opened=issues_opened,
        issues_closed=issues_closed,
        discussion=discussion,
        narrative="\n".join(narrative_lines).strip(),
    )


def render_body_sections(sections: BodySections) -> str:
    """Render the locked layout. Empty sections (and the ``## Activity``
    parent when all subsections are empty) are omitted entirely."""
    parts: list[str] = []
    parts.append(f"# {sections.title}\n")

    if sections.summary:
        parts.append("\n## Summary\n\n")
        parts.append(sections.summary.rstrip() + "\n")

    if sections.narrative:
        parts.append("\n## Narrative\n\n")
        parts.append(sections.narrative.rstrip() + "\n")

    if sections.discussion:
        parts.append("\n## Discussion\n\n")
        parts.extend(line + "\n" for line in sections.discussion)

    if sections.adr_candidates:
        parts.append(f"\n## ADR candidates\n\n{_ADR_GLOSS}\n")
        parts.extend(line + "\n" for line in sections.adr_candidates)

    if sections.worked_on:
        parts.append("\n## What we worked on\n\n")
        parts.extend(line + "\n" for line in sections.worked_on)

    activity_subs: list[tuple[str, list[str]]] = [
        ("### Commits", sections.commits),
        ("### Issues opened", sections.issues_opened),
        ("### Issues closed", sections.issues_closed),
    ]
    if any(items for _, items in activity_subs):
        parts.append("\n## Activity\n")
        for heading, items in activity_subs:
            if not items:
                continue
            parts.append(f"\n{heading}\n\n")
            parts.extend(line + "\n" for line in items)

    if sections.loose_ends:
        parts.append("\n## Loose ends\n\n")
        parts.extend(line + "\n" for line in sections.loose_ends)

    return "".join(parts).rstrip() + "\n"


def merge_body_sections(existing: BodySections, new: BodySections) -> BodySections:
    """Merge a new chunk's sections into an existing note's sections.

    Title and Summary are sticky — the existing values win. An empty new
    chunk must never blank out an existing summary, and without an LLM
    in the loop a later chunk's framing is no more authoritative than
    the earlier one.

    Bullet lists are unioned by appending; exact-match dups drop so a
    new chunk's collector pass that re-discovers a commit seen in an
    earlier chunk doesn't double-list it.
    """
    return BodySections(
        title=existing.title or new.title,
        summary=existing.summary or new.summary,
        adr_candidates=_dedup_lines(existing.adr_candidates, new.adr_candidates),
        worked_on=_dedup_lines(existing.worked_on, new.worked_on),
        loose_ends=_dedup_lines(existing.loose_ends, new.loose_ends),
        commits=_dedup_lines(existing.commits, new.commits),
        issues_opened=_dedup_lines(existing.issues_opened, new.issues_opened),
        issues_closed=_dedup_lines(existing.issues_closed, new.issues_closed),
        discussion=_dedup_lines(existing.discussion, new.discussion),
        narrative=existing.narrative or new.narrative,
    )


def _dedup_lines(*sources: list[str]) -> list[str]:
    """Concatenate ``sources`` then drop later occurrences of any
    already-seen line. Order is the first-seen order across the inputs.
    """
    seen: set[str] = set()
    out: list[str] = []
    for source in sources:
        for line in source:
            if line in seen:
                continue
            seen.add(line)
            out.append(line)
    return out


@dataclass
class FiledNote:
    path: Path
    wikilink: str               # e.g. "[[19-add-ledger]]"
    was_merge: bool             # True if appended to an existing note


# ---- path parsing (public — consumed by lint + hooks status-line) ----------


_SESSION_FILENAME_RE = re.compile(
    r"^(?P<day>\d{2})(?:-(?P<hhmm>\d{4}))?-(?P<slug>.+)\.md$"
)


def session_path_sort_key(
    path: Path,
) -> tuple[int, int, int, int, str]:
    """Sort key for session-note paths under the sharded layout.

    Returns ``(year, month, day, hhmm, slug)`` — sortable tuple where
    ascending order is oldest→newest. Reverse the sort to render
    newest-first lists.

    Both filename shapes are recognized:

    - New: ``DD-HHMM-slug.md`` → ``(Y, M, D, HHMM, slug)``
    - Legacy: ``DD-slug.md``   → ``(Y, M, D, 0,    slug)``

    Legacy entries deliberately collapse to ``hhmm=0`` so they sort
    *first* within their day in ascending order — equivalently, *last*
    within their day in reverse order. Since we don't know what time
    of day a legacy note was filed, treating it as "earliest" avoids
    falsely surfacing it ahead of timed peers.

    Year/month come from the parent directories. Files outside the
    expected ``YYYY/MM/`` shape return ``(0, 0, 0, 0, name)`` so they
    sink to the bottom of any ranking rather than crashing.
    """
    parent = path.parent
    try:
        month = int(parent.name)
        year = int(parent.parent.name)
    except (ValueError, AttributeError):
        return (0, 0, 0, 0, path.name)

    m = _SESSION_FILENAME_RE.match(path.name)
    if m is None:
        return (year, month, 0, 0, path.name)

    day = int(m.group("day"))
    hhmm = int(m.group("hhmm")) if m.group("hhmm") else 0
    slug = m.group("slug")
    return (year, month, day, hhmm, slug)
