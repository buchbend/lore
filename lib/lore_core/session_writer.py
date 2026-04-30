"""Shared session-note writer — the single place a session note is filed.

Both the explicit `/lore:session` path (via `lore_core/session.py`) and
the passive curator-A path (via `lore_curator/session_filer.py`) funnel
into `file_or_merge` here. Differences between the two flows — who
composes the body, whether transcript provenance is present, whether an
LLM wrote anything — are captured in the `SessionInput` dataclass;
everything else (path, append-to-today merge rule, frontmatter render,
atomic write) lives here.

Layout invariant
----------------

Every session note lives at::

    <wiki>/sessions[/<handle>]/<YYYY>/<MM>/<DD>-<HHMM>-<slug>.md

``<handle>`` is present iff the wiki is in team mode (``_users.yml``
exists — see `lore_core.identity.session_note_dir`). Append-to-today
searches the *handle-scoped* month directory so concurrent authors
don't collide.

``<HHMM>`` is the file-time of the *first* chunk of a session note
(zero-padded 24h, e.g. ``1432``). Subsequent chunks merging into the
same note keep the original prefix — the prefix marks "when this
session started", not "when the last edit landed". Legacy notes filed
before the v0.20.x cutover live at the older ``<DD>-<slug>.md`` shape;
readers (lint, status-line, walkers) accept both.

Transcripts cap
---------------

Session notes carry a ``transcripts:`` list of source UUIDs for passive
capture. The list is capped at 20 most-recent UUIDs. See
`lore_curator/session_filer.py` docstring for the full provenance
contract.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date as _date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, NamedTuple

import yaml

from lore_core.identity import session_note_dir
from lore_core.io import atomic_write_text
from lore_core.schema import parse_frontmatter
from lore_core.types import Scope, TranscriptHandle

if TYPE_CHECKING:
    from lore_core.run_log import RunLogger


_TRANSCRIPTS_CAP = 20


# Topic-signal-vs-boilerplate classification moved to lore_core.topic_files
# in v0.8.2 once two consumers needed it. Re-exported here under the
# previous private names for backward compat with any external callers.
from lore_core.topic_files import (
    BOILERPLATE_FILES as _TOPIC_BOILERPLATE_FILES,  # noqa: F401
    strip_boilerplate as _strip_boilerplate,
)

# Jaccard threshold above which two file-sets are "the same topic" and
# the new chunk merges into the open note. Raised from 0.3 → 0.5 after
# a real-world Frankenstein merge: two semantically-distinct sessions
# (GitHub-issue curation vs. a step_files plan) shared a single
# incidentally-touched helper module (`hooks.py`) and cleared 0.3 with
# 1/3 ≈ 0.33, then collided their summaries. 0.5 keeps the two-of-three
# continuation case (auth.py + auth_test.py / auth.py + helpers.py
# fails; auth.py + auth_test.py + a / auth.py + auth_test.py + b
# passes) but rejects the single-shared-file false-positive class.
_TOPIC_OVERLAP_MIN_JACCARD = 0.5


def _dedup_preserving_order(items: list[str] | None) -> list[str]:
    """First-seen order; truthy strings only, non-string entries dropped.

    Used by frontmatter writers so ``files_touched`` / ``transcripts``
    style fields produce stable, readable diffs across appends.
    """
    if not items:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if isinstance(item, str) and item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


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
# Append-mode merges a new chunk into an existing note's sections rather
# than wrapping each chunk in its own ``## <chunk title>`` H2. The parser
# below is permissive — unrecognised content is dropped (legacy
# ``### Files touched`` / ``Entities:`` lines fade out as old notes are
# touched), but recognised section content round-trips losslessly.
# ---------------------------------------------------------------------------


class BodySections(NamedTuple):
    """Structured view of a session-note body for parse → merge → render."""

    title: str
    summary: str           # paragraph (multiline allowed; no leading "- ")
    decisions: list[str]   # bullet lines including their leading "- "
    worked_on: list[str]
    loose_ends: list[str]
    commits: list[str]     # under ### Commits
    issues_opened: list[str]
    issues_closed: list[str]


_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$")
_KNOWN_H2: dict[str, str] = {
    "Summary": "summary",
    "Decisions made": "decisions",
    "What we worked on": "worked_on",
    "Activity": "activity",
    "Loose ends": "loose_ends",
}
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
    decisions: list[str] = []
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
        elif current_h2 == "decisions" and line.lstrip().startswith("-"):
            decisions.append(line)
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
        decisions=decisions,
        worked_on=worked_on,
        loose_ends=loose_ends,
        commits=commits,
        issues_opened=issues_opened,
        issues_closed=issues_closed,
    )


def render_body_sections(sections: BodySections) -> str:
    """Render the locked layout. Empty sections (and the ``## Activity``
    parent when all subsections are empty) are omitted entirely."""
    parts: list[str] = []
    parts.append(f"# {sections.title}\n")

    if sections.summary:
        parts.append("\n## Summary\n\n")
        parts.append(sections.summary.rstrip() + "\n")

    if sections.decisions:
        parts.append("\n## Decisions made\n\n")
        parts.extend(line + "\n" for line in sections.decisions)

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

    Default rule: Title and Summary are sticky — the existing values
    win. This is the safe deterministic primitive: an empty new chunk
    must never blank out an existing summary, and without an LLM in the
    loop a later chunk's framing is no more authoritative than the
    earlier one.

    Curator A overrides the summary via an LLM merge in
    ``_append_to_note`` when ``SessionInput.summary_merger`` is set —
    that path composes a 1-2 sentence summary that anchors on the
    existing framing and weaves in the new chunk's context. The merge
    function lives in ``lore_curator.summary_merge`` so the writer
    stays free of LLM dependencies; the writer just calls the closure.

    Bullet lists are unioned by appending; exact-match dups drop so a
    new chunk's collector pass that re-discovers a commit seen in an
    earlier chunk doesn't double-list it.
    """
    return BodySections(
        title=existing.title or new.title,
        summary=existing.summary or new.summary,
        decisions=_dedup_lines(existing.decisions, new.decisions),
        worked_on=_dedup_lines(existing.worked_on, new.worked_on),
        loose_ends=_dedup_lines(existing.loose_ends, new.loose_ends),
        commits=_dedup_lines(existing.commits, new.commits),
        issues_opened=_dedup_lines(existing.issues_opened, new.issues_opened),
        issues_closed=_dedup_lines(existing.issues_closed, new.issues_closed),
    )


# (existing_summary, new_summary, new_worked_on_bullets, new_decisions) -> merged_summary
SummaryMerger = Callable[[str, str, list[str], list[str]], str]


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


def _topic_jaccard(a: list[str] | None, b: list[str] | None) -> float:
    """Jaccard similarity between two file-set lists, ignoring boilerplate.

    Returns 0.0 for either side empty (no signal). Returns 1.0 if both
    sides are equal non-empty sets.
    """
    sa = _strip_boilerplate(a)
    sb = _strip_boilerplate(b)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


@dataclass
class SessionInput:
    """Inputs common to both the explicit and passive session flows.

    Required for every call::

        scope, wiki_root, work_time, handle, slug, description, body_markdown

    ``handle`` may be empty string — in solo mode that's fine; in team
    mode, an empty handle will skip sharding and the note will live in
    the flat ``sessions/YYYY/MM/…`` directory (caller's responsibility
    to pass the right handle).
    """

    scope: Scope
    wiki_root: Path
    work_time: datetime
    handle: str
    slug: str
    description: str
    body_markdown: str
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    # Content-named headline used for body H1 and append-mode H2 boundaries.
    # Falls back to ``description`` when omitted (e.g. the explicit /lore:session
    # path before users start writing a separate ``title:`` field).
    title: str = ""
    tags: list[str] = field(default_factory=list)
    extra_frontmatter: dict[str, Any] = field(default_factory=dict)

    # Passive-capture provenance (omit for explicit writes).
    transcript: TranscriptHandle | None = None
    turn_hashes: tuple[str | None, str | None] | None = None
    scope_redirected_from: str | None = None

    # Phase C: file paths actually touched by this chunk's tool calls.
    # Persisted in frontmatter so future chunks can decide topic-merge
    # via file-set overlap. Already-stripped of boilerplate before being
    # handed in is fine but not required — the merge logic strips again.
    files_touched: list[str] = field(default_factory=list)

    # Phase 3 — auto-populated cross-note linkage. ``plans`` are
    # ``<slug>#s<N>`` refs (or bare ``<slug>``); ``projects`` are
    # bare project-note slugs. Both are validated against the wiki's
    # plans/ and projects/ dirs by the filer; hallucinated refs are
    # dropped before they reach SessionInput.
    plans: list[str] = field(default_factory=list)
    projects: list[str] = field(default_factory=list)

    # Phase 3 — pre-rendered Activity bullets. These flow into the body
    # ``## Activity`` parent (with ``### Commits`` / ``### Issues
    # opened`` / ``### Issues closed`` subheadings) via the locked
    # body-section shape.
    activity_commits: list[str] = field(default_factory=list)
    activity_issues_opened: list[str] = field(default_factory=list)
    activity_issues_closed: list[str] = field(default_factory=list)

    # Optional LLM-backed summary merger. When set AND this call ends
    # up appending to an existing note, ``_append_to_note`` invokes the
    # closure with the existing+new summaries (and the new chunk's
    # bullets/decisions for context) to compose a merged 1-2 sentence
    # summary. The result drives both the body ``## Summary`` and
    # frontmatter ``description``. None (the default) preserves the
    # deterministic sticky-existing fallback used by tests, the
    # explicit /lore:session path, and dry-run / no-LLM curator runs.
    summary_merger: SummaryMerger | None = None


@dataclass
class FiledNote:
    path: Path
    wikilink: str               # e.g. "[[19-add-ledger]]"
    was_merge: bool             # True if appended to an existing note


def file_or_merge(
    si: SessionInput,
    *,
    logger: "RunLogger | None" = None,
    transcript_id: str | None = None,
) -> FiledNote:
    """Create a new session note or append to today's open note.

    ``transcript_id`` is only used for logging when provided — it can
    differ from ``si.transcript.id`` in tests.
    """
    sessions_base = session_note_dir(si.wiki_root, si.handle)
    month_dir = _month_dir(sessions_base, si.work_time)
    month_dir.mkdir(parents=True, exist_ok=True)

    today_note = _find_todays_open_note(
        sessions_base, scope=si.scope, work_date=si.work_time.date(),
        new_files_touched=si.files_touched,
    )
    if today_note is not None:
        if logger is not None:
            logger.emit(
                "merge-check",
                transcript_id=transcript_id,
                target=f"[[{today_note.stem}]]",
                similarity=None,
                decision="append-today",
            )
        _append_to_note(today_note, si)
        wikilink = f"[[{today_note.stem}]]"
        if logger is not None:
            logger.emit(
                "session-note",
                transcript_id=transcript_id,
                action="merged",
                wikilink=wikilink,
            )
        return FiledNote(path=today_note, wikilink=wikilink, was_merge=True)

    day_prefix = f"{si.work_time.day:02d}"
    time_prefix = si.work_time.strftime("%H%M")
    path = month_dir / f"{day_prefix}-{time_prefix}-{si.slug}.md"
    counter = 1
    while path.exists():
        counter += 1
        path = month_dir / f"{day_prefix}-{time_prefix}-{si.slug}-{counter}.md"

    _write_new_note(path, si)
    wikilink = f"[[{path.stem}]]"
    if logger is not None:
        logger.emit(
            "session-note",
            transcript_id=transcript_id,
            action="filed",
            path=str(path),
            wikilink=wikilink,
        )
    return FiledNote(path=path, wikilink=wikilink, was_merge=False)


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


# ---- private helpers --------------------------------------------------------


def _month_dir(sessions_base: Path, work_time: datetime) -> Path:
    return sessions_base / str(work_time.year) / f"{work_time.month:02d}"


def _find_todays_open_note(
    sessions_base: Path,
    *,
    scope: Scope,
    work_date: _date,
    new_files_touched: list[str] | None = None,
) -> Path | None:
    """Find a same-day same-scope open note that the new chunk should
    merge into.

    Phase C: when ``new_files_touched`` is given AND any candidate has
    its own ``files_touched`` frontmatter, require a Jaccard overlap of
    at least :data:`_TOPIC_OVERLAP_MIN_JACCARD` (boilerplate-stripped)
    so disjoint topics on the same day end up in different notes.

    Backward compat: if either side has no ``files_touched`` info
    (legacy notes pre-Phase-C, or talk-only chunks with no tool calls),
    fall through to the pre-Phase-C "most recent same-day same-scope
    note" rule. This preserves existing behaviour for anything filed
    before the upgrade.
    """
    month = sessions_base / str(work_date.year) / f"{work_date.month:02d}"
    if not month.exists():
        return None
    day_prefix = f"{work_date.day:02d}"
    new_set = _strip_boilerplate(new_files_touched)

    # (-jaccard, -mtime, path) — best topic match first; ties break on
    # most-recent. Negative values let us sort ascending.
    candidates: list[tuple[float, float, Path]] = []
    for p in month.glob(f"{day_prefix}-*.md"):
        try:
            text = p.read_text()
        except OSError:
            continue
        fm = parse_frontmatter(text)
        if fm.get("scope") != scope.scope:
            continue
        if fm.get("closed"):
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue

        candidate_files = fm.get("files_touched") or []
        candidate_set = _strip_boilerplate(
            candidate_files if isinstance(candidate_files, list) else []
        )

        # Decision rules:
        # - Both sides have non-empty file sets → require Jaccard ≥ threshold.
        # - New chunk has files but candidate has none (legacy / talk-only):
        #   refuse to merge. On the upgrade day, a single legacy note
        #   would otherwise become an attractor for every new file-bearing
        #   chunk regardless of topic — exactly the Frankenstein note we
        #   want to avoid.
        # - New chunk is talk-only: no signal to discriminate topics, so
        #   fall back to pre-Phase-C "most-recent same-day same-scope"
        #   matching. Continuing-the-conversation case.
        if new_set and candidate_set:
            jac = len(new_set & candidate_set) / len(new_set | candidate_set)
            if jac < _TOPIC_OVERLAP_MIN_JACCARD:
                continue
            candidates.append((-jac, -mtime, p))
        elif new_set and not candidate_set:
            # Skip — don't attract file-bearing chunks into ambiguous legacy.
            continue
        else:
            candidates.append((0.0, -mtime, p))

    if not candidates:
        return None
    candidates.sort()
    return candidates[0][2]


def _build_frontmatter(si: SessionInput) -> dict[str, Any]:
    from_hash = si.turn_hashes[0] if si.turn_hashes else None
    to_hash = si.turn_hashes[1] if si.turn_hashes else None

    fm: dict[str, Any] = {
        "schema_version": 2,
        "type": "session",
        "created": si.work_time.date().isoformat(),
        "last_reviewed": si.work_time.date().isoformat(),
    }
    # Title is the content-named slug source; description is the 1-2-sentence
    # status-line preview. Title may be absent on legacy explicit-path callers
    # that haven't been updated yet — in that case description carries both
    # roles, same as before.
    if si.title:
        fm["title"] = si.title
    fm["description"] = si.description
    fm["scope"] = si.scope.scope
    if si.handle:
        fm["user"] = si.handle
    if si.transcript is not None:
        # ``draft: true`` was vestigial — sessions are immutable historical
        # records, not living docs that flip canonical/draft. Dropped per the
        # session-note revision; old notes keep validating via permissive
        # OPTIONAL_FIELDS.
        fm["curator_a_run"] = si.now.isoformat()
        fm["source_transcripts"] = [
            {
                "integration": si.transcript.integration,
                "id": si.transcript.id,
                "from_hash": from_hash,
                "to_hash": to_hash,
            }
        ]
        fm["transcripts"] = [si.transcript.id]
    if si.tags:
        fm["tags"] = si.tags
    if si.projects:
        fm["projects"] = _dedup_preserving_order(si.projects)
    if si.plans:
        fm["plans"] = _dedup_preserving_order(si.plans)
    if si.files_touched:
        fm["files_touched"] = _dedup_preserving_order(si.files_touched)
    for k, v in si.extra_frontmatter.items():
        fm.setdefault(k, v)
    if si.scope_redirected_from:
        fm["scope_redirected_from"] = si.scope_redirected_from
    return fm


def _write_new_note(path: Path, si: SessionInput) -> None:
    from lore_core.wikilinks import sanitize_for_write

    fm = _build_frontmatter(si)
    text = _render_markdown(fm, si.body_markdown)
    text = sanitize_for_write(text, si.wiki_root)
    atomic_write_text(path, text)


def _append_to_note(path: Path, si: SessionInput) -> None:
    text = path.read_text()
    fm = parse_frontmatter(text)
    body = _strip_frontmatter(text)

    fm["last_reviewed"] = si.work_time.date().isoformat()

    if si.transcript is not None:
        from_hash = si.turn_hashes[0] if si.turn_hashes else None
        to_hash = si.turn_hashes[1] if si.turn_hashes else None

        fm["curator_a_run"] = si.now.isoformat()
        src = fm.get("source_transcripts") or []
        src.append(
            {
                "integration": si.transcript.integration,
                "id": si.transcript.id,
                "from_hash": from_hash,
                "to_hash": to_hash,
            }
        )
        fm["source_transcripts"] = src

        existing = fm.get("transcripts") or []
        if not isinstance(existing, list):
            existing = []
        uuid_list = [u for u in existing if u != si.transcript.id]
        uuid_list.append(si.transcript.id)
        if len(uuid_list) > _TRANSCRIPTS_CAP:
            uuid_list = uuid_list[-_TRANSCRIPTS_CAP:]
        fm["transcripts"] = uuid_list

    if si.scope_redirected_from and "scope_redirected_from" not in fm:
        fm["scope_redirected_from"] = si.scope_redirected_from

    # Phase C: union files_touched across appends so the next chunk's
    # merge decision compares against the full topic history.
    if si.files_touched:
        existing_files = fm.get("files_touched") or []
        if not isinstance(existing_files, list):
            existing_files = []
        fm["files_touched"] = _dedup_preserving_order(
            list(existing_files) + list(si.files_touched)
        )

    # Phase 3: union projects/plans across appends. These are the
    # cross-note linkage fields that Curator A re-derives per chunk
    # from cwd repo / files_touched / Plan: trailers / body wikilinks.
    # On append we keep prior-chunk refs in case the new chunk's
    # collectors miss a previously-seen plan or project.
    if si.projects:
        existing_projects = fm.get("projects") or []
        if not isinstance(existing_projects, list):
            existing_projects = []
        fm["projects"] = _dedup_preserving_order(
            list(existing_projects) + list(si.projects)
        )
    if si.plans:
        existing_plans = fm.get("plans") or []
        if not isinstance(existing_plans, list):
            existing_plans = []
        fm["plans"] = _dedup_preserving_order(
            list(existing_plans) + list(si.plans)
        )

    # Phase 2 append rule: parse both bodies into the locked section
    # shape and merge bullet lists. The new chunk's bullets append to
    # the corresponding sections (additive, deterministic).
    #
    # Summary/description, by contrast, are *not* additive — naively
    # concatenating two paragraphs makes the note unreadable. Curator A
    # passes a ``summary_merger`` closure that asks the LLM to compose a
    # 1-2 sentence summary anchored on the existing framing with the
    # new chunk's context worked in. That output drives BOTH the body
    # ``## Summary`` and the frontmatter ``description`` (passive
    # capture mirrors the two — see session_filer). When no merger is
    # supplied (tests, dry-run, explicit /lore:session path), fall back
    # to the deterministic sticky-existing rule: existing summary wins,
    # description backfills only when the existing note has none.
    #
    # Legacy-shape fallback: when the existing note pre-dates the
    # locked shape (no recognisable H2 sections), the parser returns
    # an empty BodySections. The summary merger short-circuits when
    # existing is empty (see summary_merge.merge_descriptions), so the
    # new chunk's framing is preserved without an LLM round-trip.
    existing_sections = parse_body_sections(body)
    new_sections = parse_body_sections(si.body_markdown)
    merged = merge_body_sections(existing_sections, new_sections)

    if si.summary_merger is not None:
        # Anchor on the body ``## Summary`` if present; otherwise fall
        # back to frontmatter ``description``. In current notes the two
        # are mirrored (body Summary is set from noteworthy.description),
        # but legacy notes may have description-only or summary-only —
        # prefer whichever is non-empty as the anchor so the merger
        # always sees the truest framing of earlier work.
        existing_anchor = existing_sections.summary or str(fm.get("description") or "")
        new_worked_on = [_strip_bullet_marker(b) for b in new_sections.worked_on]
        new_decisions = [_strip_bullet_marker(d) for d in new_sections.decisions]
        merged_summary = si.summary_merger(
            existing_anchor,
            new_sections.summary,
            new_worked_on,
            new_decisions,
        )
        if merged_summary:
            merged = merged._replace(summary=merged_summary)
            fm["description"] = merged_summary
        elif si.description and not fm.get("description"):
            # Defensive: empty merger output on a legacy description-less
            # note. Backfill from the new chunk so the note ends up with
            # SOME framing rather than nothing.
            fm["description"] = si.description
    elif si.description and not fm.get("description"):
        # No merger → backfill description only on legacy notes that
        # never had one. Existing description wins otherwise; the body
        # Summary follows the sticky rule in merge_body_sections.
        fm["description"] = si.description

    from lore_core.wikilinks import sanitize_for_write

    text_new = _render_markdown(fm, render_body_sections(merged))
    text_new = sanitize_for_write(text_new, si.wiki_root)
    atomic_write_text(path, text_new)


def _strip_bullet_marker(line: str) -> str:
    """Drop the leading ``- `` (or ``* ``) from a bullet line for prompts.

    Bullet sections store raw lines including their marker so the renderer
    can round-trip them losslessly. The merge prompt wants the bullet
    *content* without the marker noise.
    """
    stripped = line.lstrip()
    for marker in ("- ", "* "):
        if stripped.startswith(marker):
            return stripped[len(marker):]
    return stripped


from lore_core.schema import strip_frontmatter as _strip_frontmatter  # noqa: E402, F401


def _render_markdown(fm: dict[str, Any], body: str) -> str:
    dumped = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{dumped}\n---\n\n{body.rstrip()}\n"
