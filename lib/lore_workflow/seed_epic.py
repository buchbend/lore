"""Seed-epic Origin/Findings lift — pull from the session note, not freehand.

`compose_seed_lift` gives the seed-epic skill's "Write the seed(s)" step a
deterministic Origin (from the note's linkage) and Findings (from the
note's topic-chapter bodies) instead of a model reconstructing them from
memory. Returns ``None`` — the caller's signal to fall back to the existing
freehand path — when there is no note, or the note carries nothing a
freehand pass wouldn't already have: no linkage refs and no topic-chapter
content. Marker chapters (withheld/failed) are gate bookkeeping, not
findings, and don't count either way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from lore_core.note_document import DISCLAIMER, NoteView, read_note

__all__ = ["SeedLift", "compose_seed_lift"]

_CHAPTER_HEADER_RE = re.compile(
    r"^<!-- lore:chapter (\d+)(?: marker:\S+)? @\d+-\d+ -->\s*$", re.MULTILINE
)


@dataclass(frozen=True)
class SeedLift:
    """Origin + Findings lifted from a session note, plus a pointer back to it."""

    origin: str
    findings: str
    source_note: str


def _format_origin(linkage: dict) -> str:
    repo = linkage.get("repo") or ""
    if not repo:
        return ""
    bits = [f"`{repo}`"]
    epics = linkage.get("epics") or []
    if epics:
        bits.append("epic " + ", ".join(f"#{n}" for n in epics))
    prs = linkage.get("prs") or []
    if prs:
        label = "PR" if len(prs) == 1 else "PRs"
        bits.append(f"{label} " + ", ".join(f"#{n}" for n in prs))
    return " — ".join(bits)


def _findings(view: NoteView) -> str:
    """Concatenate topic-chapter bodies; marker chapters carry no findings."""
    topic_ns = {c["n"] for c in view.chapters if c.get("kind") == "topic"}
    if not topic_ns:
        return ""
    body = view.body
    if body.startswith(DISCLAIMER):
        body = body[len(DISCLAIMER) :]
    matches = list(_CHAPTER_HEADER_RE.finditer(body))
    segments = []
    for i, m in enumerate(matches):
        if int(m.group(1)) not in topic_ns:
            continue
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        segment = body[start:end].strip()
        if segment:
            segments.append(segment)
    return "\n\n".join(segments)


def compose_seed_lift(note_path: Path, *, wiki_root: Path | None = None) -> SeedLift | None:
    """Lift Origin/Findings from the note at ``note_path``.

    Returns ``None`` when the note is missing or too thin (no linkage refs,
    no topic-chapter findings) — the caller's signal to fall back to the
    existing freehand path.
    """
    if not note_path.exists():
        return None
    view = read_note(note_path)
    origin = _format_origin(view.frontmatter.get("linkage") or {})
    findings = _findings(view)
    if not origin and not findings:
        return None
    source_note = str(note_path.relative_to(wiki_root)) if wiki_root else str(note_path)
    return SeedLift(origin=origin, findings=findings, source_note=source_note)
