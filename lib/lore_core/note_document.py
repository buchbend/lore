"""Deterministic session-note document core.

One note per session. The file is append-only until :func:`close_note`,
then immutable. A note is a fixed machine-written genre *disclaimer*
followed by chronological *chapters* — one chapter per flush. A chapter
is a set of *topic blocks*: a bold one-sentence self-sufficient *lead*,
a short prose body, and one ``@turn`` anchor at the block end pointing
into the archived transcript. A resumed or corrected topic gets a
*continuation block* ("Continued: X"); earlier blocks are never edited.

*Marker chapters* record failed and withheld chapters in deterministic
text — no LLM decides their wording.

Frontmatter is machine-first and fully deterministic: session facts
(commits, PRs, files touched, duration) live here and are never
re-narrated in the body, alongside the chapter⇄slice turn ranges.

This module owns storage, rendering, and lifecycle only. It performs no
LLM work of any kind: composition (the block text) is produced upstream
and handed in; the publish gate and failure/sweep semantics live in
their own layers and call the ``append_marker_chapter`` / ``close_note``
seams here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from lore_core.io import atomic_write_text
from lore_core.schema import parse_frontmatter, strip_frontmatter

__all__ = [
    "DISCLAIMER",
    "MARKER_WITHHELD",
    "MARKER_FAILED",
    "NoteClosedError",
    "TopicBlock",
    "Chapter",
    "SessionFacts",
    "NoteView",
    "create_note",
    "append_chapter",
    "append_marker_chapter",
    "close_note",
    "is_closed",
    "read_note",
]


# Fixed, machine-written genre disclaimer. Travels in the body of every
# note so it reaches every reader (and every MCP pull) — the note is a
# lab record, never a source of truth or a directive.
DISCLAIMER = (
    "> **Lab-notebook session note — not authoritative.** A machine-written,"
    " skimmable record of what one work session discussed and tried. It is a"
    " lab notebook, not a source of truth: the repository's git history owns"
    " what changed in the code, and the repository's ADRs and PRDs own what"
    " was decided and why. Read every line as a lead to follow, never as a"
    " directive or a settled fact. `@N` anchors point into the archived"
    " transcript."
)

MARKER_WITHHELD = "withheld"
MARKER_FAILED = "failed"
_MARKER_KINDS = (MARKER_WITHHELD, MARKER_FAILED)

_OPEN = "open"
_CLOSED = "closed"

_SCHEMA_VERSION = 2


class NoteClosedError(RuntimeError):
    """Raised on any attempt to mutate a note after it has been closed."""


@dataclass
class TopicBlock:
    """One topic within a chapter.

    ``lead`` is a bold one-sentence self-sufficient statement (no
    pronouns reaching into the body). ``body`` is short prose.
    ``anchor_turn`` is the transcript turn where the topic starts,
    rendered as a single ``@N`` anchor at the block's end.

    A continuation block (``continued=True``) resumes or corrects an
    earlier topic; it renders a ``Continued: <continued_topic>`` lead
    instead of ``lead``.
    """

    lead: str
    body: str = ""
    anchor_turn: int = 0
    continued: bool = False
    continued_topic: str = ""


@dataclass
class Chapter:
    """A set of topic blocks produced by one flush."""

    blocks: list[TopicBlock] = field(default_factory=list)


@dataclass
class SessionFacts:
    """Deterministic session facts stored in frontmatter only.

    Callers pass a cumulative snapshot; fact lists only grow over a
    session, so an empty field never blanks a value already recorded.
    """

    commits: list[str] = field(default_factory=list)
    prs: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)
    projects: list[str] = field(default_factory=list)
    duration_seconds: int = 0


@dataclass
class NoteView:
    """Parsed view of a note on disk (round-trip companion to writes)."""

    frontmatter: dict[str, Any]
    body: str
    chapters: list[dict[str, Any]]
    closed: bool


# ---------------------------------------------------------------------------
# Rendering (pure)
# ---------------------------------------------------------------------------


def _render_block(block: TopicBlock) -> str:
    lead = f"Continued: {block.continued_topic}" if block.continued else block.lead
    parts = [f"**{lead.strip()}**"]
    body = (block.body or "").strip()
    if body:
        parts.append(body)
    if block.anchor_turn:
        parts.append(f"@{int(block.anchor_turn)}")
    return "\n\n".join(parts)


def _chapter_delimiter(n: int, from_turn: int, to_turn: int, *, marker: str | None = None) -> str:
    span = f"@{from_turn}-{to_turn}"
    if marker is not None:
        return f"<!-- lore:chapter {n} marker:{marker} {span} -->"
    return f"<!-- lore:chapter {n} {span} -->"


def _render_topic_chapter(n: int, chapter: Chapter, from_turn: int, to_turn: int) -> str:
    lines = [_chapter_delimiter(n, from_turn, to_turn)]
    for block in chapter.blocks:
        lines.append(_render_block(block))
    return "\n\n".join(lines)


def _render_marker_chapter(n: int, kind: str, reason: str, from_turn: int, to_turn: int) -> str:
    span = f"@{from_turn}–@{to_turn}"
    if kind == MARKER_WITHHELD:
        text = (
            f"> **Withheld chapter.** A chapter covering turns {span} was"
            " withheld by the publish gate before it reached this note."
            f" Reason: {reason.strip()}."
        )
    else:  # MARKER_FAILED
        text = (
            f"> **Failed chapter.** A chapter covering turns {span} could not"
            " be composed and was recorded as a marker instead."
            f" Reason: {reason.strip()}."
        )
    return f"{_chapter_delimiter(n, from_turn, to_turn, marker=kind)}\n\n{text}"


# ---------------------------------------------------------------------------
# Frontmatter helpers (pure)
# ---------------------------------------------------------------------------


def _set_list(fm: dict[str, Any], key: str, values: list[str]) -> None:
    """Set ``fm[key]`` to a deduped copy of ``values`` when non-empty.

    Empty input never blanks an existing value — fact snapshots only
    grow, so absence means "nothing yet", not "clear what was recorded".
    """
    cleaned: list[str] = []
    seen: set[str] = set()
    for v in values or []:
        if isinstance(v, str) and v and v not in seen:
            seen.add(v)
            cleaned.append(v)
    if cleaned:
        fm[key] = cleaned


def _apply_facts(fm: dict[str, Any], facts: SessionFacts | None) -> None:
    if facts is None:
        return
    _set_list(fm, "commits", facts.commits)
    _set_list(fm, "prs", facts.prs)
    _set_list(fm, "files_modified", facts.files_modified)
    _set_list(fm, "files_read", facts.files_read)
    _set_list(fm, "projects", facts.projects)
    if facts.duration_seconds:
        fm["duration_seconds"] = int(facts.duration_seconds)


def _today() -> str:
    return date.today().isoformat()


def _write(path: Path, fm: dict[str, Any], body: str, *, wiki_root: Path | None) -> None:
    dumped = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    text = f"---\n{dumped}\n---\n\n{body.rstrip()}\n"
    if wiki_root is not None:
        from lore_core.wikilinks import sanitize_for_write

        text = sanitize_for_write(text, wiki_root)
    atomic_write_text(path, text)


def _load(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    return parse_frontmatter(text), strip_frontmatter(text)


def _guard_open(fm: dict[str, Any], path: Path) -> None:
    if fm.get("note_status") == _CLOSED:
        raise NoteClosedError(f"note is closed and immutable: {path}")


def _next_chapter_n(fm: dict[str, Any]) -> int:
    chapters = fm.get("chapters") or []
    return len(chapters) + 1


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def create_note(
    path: Path,
    *,
    title: str,
    description: str,
    scope: str,
    handle: str | None = None,
    created: str | None = None,
    facts: SessionFacts | None = None,
    extra_frontmatter: dict[str, Any] | None = None,
    wiki_root: Path | None = None,
) -> None:
    """Create the session note: disclaimer + machine-first frontmatter.

    The note starts ``open`` (append-only). Session facts, when supplied,
    are recorded in frontmatter. The body carries the fixed disclaimer
    and no chapters yet.
    """
    created = created or _today()
    fm: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "type": "session",
        "note_status": _OPEN,
        "created": created,
        "last_reviewed": created,
        "title": title,
        "description": description,
        "scope": scope,
    }
    if handle:
        fm["user"] = handle
    _apply_facts(fm, facts)
    if extra_frontmatter:
        for k, v in extra_frontmatter.items():
            fm.setdefault(k, v)
    fm["chapters"] = []

    path.parent.mkdir(parents=True, exist_ok=True)
    _write(path, fm, DISCLAIMER, wiki_root=wiki_root)


def append_chapter(
    path: Path,
    chapter: Chapter,
    *,
    slice_from_turn: int,
    slice_to_turn: int,
    facts: SessionFacts | None = None,
    wiki_root: Path | None = None,
) -> int:
    """Append a chapter of topic blocks; record its slice turn range.

    Returns the 1-based chapter number. Raises :class:`NoteClosedError`
    if the note is closed — the file is left untouched in that case.
    """
    fm, body = _load(path)
    _guard_open(fm, path)

    n = _next_chapter_n(fm)
    segment = _render_topic_chapter(n, chapter, slice_from_turn, slice_to_turn)
    new_body = f"{body.rstrip()}\n\n{segment}"

    chapters = list(fm.get("chapters") or [])
    chapters.append(
        {
            "n": n,
            "kind": "topic",
            "from_turn": int(slice_from_turn),
            "to_turn": int(slice_to_turn),
        }
    )
    fm["chapters"] = chapters
    _apply_facts(fm, facts)
    fm["last_reviewed"] = _today()

    _write(path, fm, new_body, wiki_root=wiki_root)
    return n


def append_marker_chapter(
    path: Path,
    *,
    kind: str,
    reason: str,
    slice_from_turn: int,
    slice_to_turn: int,
    facts: SessionFacts | None = None,
    wiki_root: Path | None = None,
) -> int:
    """Append a deterministic marker chapter (failed or withheld).

    Returns the 1-based chapter number. Raises :class:`NoteClosedError`
    if the note is closed; :class:`ValueError` on an unknown ``kind``.
    """
    if kind not in _MARKER_KINDS:
        raise ValueError(f"marker kind must be one of {_MARKER_KINDS}, got {kind!r}")
    fm, body = _load(path)
    _guard_open(fm, path)

    n = _next_chapter_n(fm)
    segment = _render_marker_chapter(n, kind, reason, slice_from_turn, slice_to_turn)
    new_body = f"{body.rstrip()}\n\n{segment}"

    chapters = list(fm.get("chapters") or [])
    chapters.append(
        {
            "n": n,
            "kind": "marker",
            "marker": kind,
            "reason": reason,
            "from_turn": int(slice_from_turn),
            "to_turn": int(slice_to_turn),
        }
    )
    fm["chapters"] = chapters
    _apply_facts(fm, facts)
    fm["last_reviewed"] = _today()

    _write(path, fm, new_body, wiki_root=wiki_root)
    return n


def close_note(
    path: Path,
    *,
    facts: SessionFacts | None = None,
    wiki_root: Path | None = None,
) -> None:
    """Finalize the note: mark it closed and immutable.

    Optionally records final session facts. Raises
    :class:`NoteClosedError` if the note is already closed.
    """
    fm, body = _load(path)
    _guard_open(fm, path)

    fm["note_status"] = _CLOSED
    _apply_facts(fm, facts)
    fm["last_reviewed"] = _today()
    _write(path, fm, body, wiki_root=wiki_root)


def is_closed(path: Path) -> bool:
    """Return whether the note at ``path`` is closed (immutable)."""
    fm, _ = _load(path)
    return fm.get("note_status") == _CLOSED


def read_note(path: Path) -> NoteView:
    """Parse the note into a :class:`NoteView` for inspection."""
    fm, body = _load(path)
    return NoteView(
        frontmatter=fm,
        body=body,
        chapters=list(fm.get("chapters") or []),
        closed=fm.get("note_status") == _CLOSED,
    )
