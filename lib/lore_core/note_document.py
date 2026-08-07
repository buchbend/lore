"""Deterministic session-note document core — the retained read/append seam.

The compose pipeline that built session notes was retired; nothing writes
one any more. What survives is not the note itself but two callers that
still need this module: the publish gate withholds a chapter through
:func:`append_marker_chapter`, and :func:`read_note` gives ``seed_epic``
and ``trace`` a parsed view of a note already on disk. Neither touches
the chapter, fact, or rendering machinery the compose pipeline used —
that machinery had no other caller and is gone (PRD 0013).

*Marker chapters* record failed and withheld chapters in deterministic
text — no LLM decides their wording. Every string that reaches the body
is neutralized on the way in (:func:`_neutralize_marker`): a reason is
code-owned today, but transcript content is one refactor away, and an
unescaped comment opener inside one would parse back out of the file as
a forged fact.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from lore_core.io import atomic_write_text
from lore_core.ref_verify import MISSING, UNCHECKED, VERIFIED
from lore_core.schema import parse_frontmatter, strip_frontmatter

__all__ = [
    "DISCLAIMER",
    "MARKER_WITHHELD",
    "MARKER_FAILED",
    "NoteClosedError",
    "NoteView",
    "append_marker_chapter",
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

_CLOSED = "closed"


class NoteClosedError(RuntimeError):
    """Raised on any attempt to mutate a note after it has been closed."""


@dataclass
class NoteView:
    """Parsed view of a note on disk (round-trip companion to writes)."""

    frontmatter: dict[str, Any]
    body: str
    chapters: list[dict[str, Any]]
    closed: bool


def _neutralize_marker(text: str) -> str:
    """Defuse a comment opener carried inside content bound for the body.

    A marker chapter's reason is code-owned today, but the day it carries
    an upstream string (a tool payload, a model message), a raw comment
    opener here would forge a fact for a reader that still parses markers.
    Escaping the OPENER kills that: nothing but a marker this module wrote
    can open one.
    """
    return text.replace("<!--", "&lt;!--")


def _chapter_delimiter(n: int, from_turn: int, to_turn: int, *, marker: str | None = None) -> str:
    span = f"@{from_turn}-{to_turn}"
    if marker is not None:
        return f"<!-- lore:chapter {n} marker:{marker} {span} -->"
    return f"<!-- lore:chapter {n} {span} -->"


def _render_marker_chapter(n: int, kind: str, reason: str, from_turn: int, to_turn: int) -> str:
    span = f"@{from_turn}–@{to_turn}"
    reason = _neutralize_marker(reason)
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


_STAMPS = {VERIFIED: "✓", UNCHECKED: "(unchecked)", MISSING: "(not found)"}


def _verdict_for(ref: Any, verdicts: dict[tuple[str, str], str]) -> str:
    """The verdict on one ref. A ref no verdict names counts as unchecked."""
    return verdicts.get((ref.type, ref.value), UNCHECKED)


def _ref_clause(refs: list[Any], verdicts: dict[tuple[str, str], str]) -> str:
    """A fact's pointers, each carrying its own verdict's stamp.

    No caller reaches this today — the fact ledger it once served was
    deleted with the compose pipeline. Kept as part of the retained
    note-document surface (PRD 0013) for the day a reader needs it again.
    """
    out = []
    for ref in refs:
        value = _neutralize_marker(" ".join(ref.value.split()))
        stamp = _STAMPS[_verdict_for(ref, verdicts)]
        out.append(f"{ref.type} {value} {stamp}".strip())
    return ", ".join(out)


def _load(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    return parse_frontmatter(text), strip_frontmatter(text)


def _write(path: Path, fm: dict[str, Any], body: str, *, wiki_root: Path | None) -> None:
    dumped = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    text = f"---\n{dumped}\n---\n\n{body.rstrip()}\n"
    if wiki_root is not None:
        from lore_core.wikilinks import sanitize_for_write

        text = sanitize_for_write(text, wiki_root)
    atomic_write_text(path, text)


def _guard_open(fm: dict[str, Any], path: Path) -> None:
    if fm.get("note_status") == _CLOSED:
        raise NoteClosedError(f"note is closed and immutable: {path}")


def _next_chapter_n(fm: dict[str, Any]) -> int:
    chapters = fm.get("chapters") or []
    return len(chapters) + 1


def _today() -> str:
    return date.today().isoformat()


def append_marker_chapter(
    path: Path,
    *,
    kind: str,
    reason: str,
    slice_from_turn: int,
    slice_to_turn: int,
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
    fm["last_reviewed"] = _today()

    _write(path, fm, new_body, wiki_root=wiki_root)
    return n


def read_note(path: Path) -> NoteView:
    """Parse the note into a :class:`NoteView` for inspection."""
    fm, body = _load(path)
    return NoteView(
        frontmatter=fm,
        body=body,
        chapters=list(fm.get("chapters") or []),
        closed=fm.get("note_status") == _CLOSED,
    )
