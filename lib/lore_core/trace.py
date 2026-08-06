"""Business logic for `lore trace` — correlated drill-down of one trace_id.

Reconstructs a story purely by reading the event spine
(:mod:`lore_core.spine`, #185/#188) — never writes. The steps are simply
every spine record sharing a ``trace_id``, ordered by timestamp; there is
no new storage or correlation mechanism here, only a selector that maps
the three ways a user names a trace onto that ``trace_id``.

The ``dead`` and ``last`` selectors are gone with the flush lifecycle
record they resolved through (issue #377). A caller that passes one now
gets :class:`TraceNotFound`, the same answer any other unknown selector
earns.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lore_core.note_document import read_note
from lore_core.spine import read_spine


class TraceNotFound(ValueError):
    """No trace, session, or note matches the given selector."""


@dataclass(frozen=True)
class TraceStep:
    """One spine record belonging to a flush, in the shape the renderer needs."""

    ts: str
    source: str
    event: str
    level: str
    error_code: str | None
    data: dict[str, Any]
    raw: dict[str, Any]


@dataclass(frozen=True)
class FlushTrace:
    trace_id: str
    steps: list[TraceStep]


def flush_by_trace_id(lore_root: Path, trace_id: str) -> FlushTrace:
    """Every spine record for ``trace_id``, chronological.

    A partial story simply has fewer steps — there is nothing to
    truncate, the tree ends wherever the last emitted event does.
    """
    steps = [
        TraceStep(
            ts=r.get("ts", ""),
            source=r.get("source", "?"),
            event=r.get("event", "?"),
            level=r.get("level", "info"),
            error_code=r.get("error_code"),
            data=r.get("data") or {},
            raw=r,
        )
        for r in read_spine(lore_root)
        if r.get("trace_id") == trace_id
    ]
    steps.sort(key=lambda s: s.ts)
    return FlushTrace(trace_id=trace_id, steps=steps)


def trace_ids_for_session(lore_root: Path, session_id: str) -> list[str]:
    """Distinct trace_ids touched by ``session_id``, most-recently-active first."""
    latest_ts: dict[str, str] = {}
    for r in read_spine(lore_root):
        if r.get("session_id") != session_id:
            continue
        tid = r.get("trace_id")
        if not tid:
            continue
        ts = r.get("ts", "")
        if ts > latest_ts.get(tid, ""):
            latest_ts[tid] = ts
    return sorted(latest_ts, key=lambda t: latest_ts[t], reverse=True)


def _resolve_note_path(lore_root: Path, note_ref: str) -> Path | None:
    """Resolve a note path or ``[[wikilink]]`` to a file. None if nothing matches."""
    ref = note_ref.strip()
    if ref.startswith("[[") and ref.endswith("]]"):
        ref = ref[2:-2].split("|", 1)[0].strip()

    def _try(name: str) -> Path | None:
        for candidate in (Path(name), lore_root / name, Path.cwd() / name):
            if candidate.is_file():
                return candidate
        return None

    found = _try(ref)
    if found is not None:
        return found
    if not ref.endswith(".md"):
        found = _try(f"{ref}.md")
        if found is not None:
            return found
        ref = f"{ref}.md"
    # Bare slug: search every wiki (wikilink resolution is per-wiki, but a
    # reverse CLI lookup has no wiki context yet — search all of them).
    wiki_dir = lore_root / "wiki"
    if wiki_dir.is_dir():
        matches = sorted(wiki_dir.glob(f"*/**/{ref}"))
        if matches:
            return matches[0]
    return None


def trace_id_for_note(lore_root: Path, note_ref: str) -> str | None:
    """Reverse lookup: a note's ``linkage.trace_id`` frontmatter field."""
    path = _resolve_note_path(lore_root, note_ref)
    if path is None:
        return None
    linkage = read_note(path).frontmatter.get("linkage") or {}
    tid = linkage.get("trace_id")
    return tid if isinstance(tid, str) else None


def resolve_selector(lore_root: Path, selector: str) -> list[str]:
    """Map a `lore trace` argument onto trace_id(s), newest first.

    Accepts a trace_id, a session id, or a note path / ``[[wikilink]]``.
    Anything else raises :class:`TraceNotFound`.
    """
    note_tid = trace_id_for_note(lore_root, selector)
    if note_tid is not None:
        return [note_tid]

    if any(r.get("trace_id") == selector for r in read_spine(lore_root)):
        return [selector]

    session_tids = trace_ids_for_session(lore_root, selector)
    if session_tids:
        return session_tids

    raise TraceNotFound(f"no trace, session, or note matches {selector!r}")
