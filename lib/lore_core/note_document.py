"""Deterministic session-note document core — the retained read seam.

The compose pipeline that built session notes was retired; nothing writes
one any more. What survives is the read side: :func:`read_note` gives
``seed_epic`` and ``trace`` a parsed view of a note already on disk. The
chapter, fact, rendering and marker-chapter writers had no other caller
and are gone (PRD 0013, issue 423).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lore_core.ref_verify import MISSING, UNCHECKED, VERIFIED
from lore_core.schema import parse_frontmatter, strip_frontmatter

__all__ = [
    "DISCLAIMER",
    "NoteView",
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

_CLOSED = "closed"


@dataclass
class NoteView:
    """Parsed view of a note on disk."""

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


def read_note(path: Path) -> NoteView:
    """Parse the note into a :class:`NoteView` for inspection."""
    fm, body = _load(path)
    return NoteView(
        frontmatter=fm,
        body=body,
        chapters=list(fm.get("chapters") or []),
        closed=fm.get("note_status") == _CLOSED,
    )
