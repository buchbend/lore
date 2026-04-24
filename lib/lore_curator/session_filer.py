"""Session-note writer / merger — create or merge into an existing note.

Given a `NoteworthyResult` + full `Turn` slice + existing recent session
notes in scope, creates a new session note or merges into an existing one.
Uses session-note-schema-v2 frontmatter. `draft: true`. Records source
transcripts with hash watermarks.

P4b — ``transcripts:`` frontmatter invariant
---------------------------------------------

Session notes carry a ``transcripts:`` list of source UUIDs in their
frontmatter. This list is **append-only provenance**. UUIDs must
never be *moved* between notes during merges, splits, or renames —
they tag the note's origin, not its ownership. A UUID appears on
every note that originated from it; if a later process splits a note,
both children keep the UUID.

Cap: the list is capped at 20 most-recent UUIDs (oldest drop first).
The cap is a pragmatic size-limit; for full provenance, cross-reference
with ``source_transcripts`` (which carries hash watermarks and is NOT
capped).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, date as _date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import yaml

from lore_core.io import atomic_write_text
from lore_core.schema import parse_frontmatter
from lore_core.types import Scope, TranscriptHandle, Turn
from lore_curator.noteworthy import NoteworthyResult

if TYPE_CHECKING:
    from lore_core.run_log import RunLogger


@dataclass
class FiledNote:
    path: Path
    wikilink: str                   # e.g. "[[19-add-ledger]]"
    was_merge: bool                 # True if appended to an existing note


def file_session_note(
    *,
    scope: Scope,
    handle: TranscriptHandle,
    noteworthy: NoteworthyResult,
    turns: list[Turn],
    wiki_root: Path,                # <lore_root>/wiki/<wiki_name>/
    anthropic_client,
    model_resolver: Callable[[str], str],
    now: datetime | None = None,
    work_time: datetime | None = None,
    logger: "RunLogger | None" = None,
    transcript_id: str | None = None,
    scope_redirected_from: str | None = None,
) -> FiledNote:
    """Create or merge a session note from a classified Turn slice.

    `now` is *curation* time (when we looked). `work_time` is when the
    work in the turns actually happened — drives filename date and
    frontmatter `created` / `last_reviewed`. When omitted, falls back
    to `now` (legacy behavior; callers that want accurate dates must
    pass the transcript's timestamp explicitly).
    """
    now = now or datetime.now(UTC)
    work_time = work_time or now
    sessions_dir = wiki_root / "sessions"
    month_dir = _month_dir(sessions_dir, work_time)
    month_dir.mkdir(parents=True, exist_ok=True)

    # P3': when an open session note for this scope already exists on the
    # transcript's work date, append to it directly — no LLM merge judgment,
    # no new-note-per-slice proliferation. `closed:` frontmatter (set by
    # Curator B when a note is folded into a surface) opts the note out.
    today_note = _find_todays_open_note(
        sessions_dir, scope=scope, work_date=work_time.date()
    )
    if today_note is not None:
        target_slug = today_note.stem
        if logger is not None:
            logger.emit(
                "merge-check",
                transcript_id=transcript_id,
                target=f"[[{target_slug}]]",
                similarity=None,
                decision="append-today",
            )
        _append_to_note(
            today_note,
            noteworthy=noteworthy,
            handle=handle,
            turns=turns,
            now=now,
            work_time=work_time,
            scope_redirected_from=scope_redirected_from,
        )
        wikilink = _wikilink_for(today_note)
        if logger is not None:
            logger.emit(
                "session-note",
                transcript_id=transcript_id,
                action="merged",
                wikilink=wikilink,
            )
        return FiledNote(path=today_note, wikilink=wikilink, was_merge=True)

    # New note — 1 transcript = 1 note (no cross-day LLM merge).
    slug = _slug(noteworthy.title)
    day_prefix = f"{work_time.day:02d}"
    path = month_dir / f"{day_prefix}-{slug}.md"
    counter = 1
    while path.exists():
        counter += 1
        path = month_dir / f"{day_prefix}-{slug}-{counter}.md"

    _write_new_note(
        path,
        scope=scope,
        handle=handle,
        noteworthy=noteworthy,
        turns=turns,
        now=now,
        work_time=work_time,
        scope_redirected_from=scope_redirected_from,
    )
    wikilink = _wikilink_for(path)

    if logger is not None:
        logger.emit(
            "session-note",
            transcript_id=transcript_id,
            action="filed",
            path=str(path),
            wikilink=wikilink,
        )

    return FiledNote(path=path, wikilink=wikilink, was_merge=False)


# ---- private helpers ----

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(title: str) -> str:
    """Lowercase, hyphen-separated, alphanumeric-only; trimmed. Max 60 chars."""
    s = _SLUG_RE.sub("-", title.lower()).strip("-")
    return s[:60] if s else "session"


def _month_dir(sessions_dir: Path, work_time: datetime) -> Path:
    """Return ``sessions/YYYY/MM/`` for the given work time."""
    return sessions_dir / str(work_time.year) / f"{work_time.month:02d}"


def _wikilink_for(path: Path) -> str:
    return f"[[{path.stem}]]"


def _find_todays_open_note(
    sessions_dir: Path,
    *,
    scope: Scope,
    work_date: _date,
) -> Path | None:
    """Return today's open session note for ``scope``, or ``None``.

    A note is "today's open note" when all of:
      - lives in the correct ``YYYY/MM/`` subdirectory,
      - filename prefix matches the day (e.g. ``22-*.md``),
      - frontmatter ``scope`` equals ``scope.scope``,
      - frontmatter ``closed`` is absent or falsy.

    When a counter-suffixed collision produced multiple candidates, the
    most-recently-modified file wins — that is the note the previous
    slice appended to.
    """
    month = sessions_dir / str(work_date.year) / f"{work_date.month:02d}"
    if not month.exists():
        return None
    day_prefix = f"{work_date.day:02d}"
    candidates: list[tuple[float, Path]] = []
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
        candidates.append((mtime, p))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def _recent_session_notes(
    sessions_dir: Path,
    *,
    scope: Scope,
    within_days: int,
    limit: int,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Return list of {path, frontmatter, preview} for recent notes in scope.

    Sorted newest first. `within_days` filters by `created` in frontmatter
    (falls back to mtime if missing).
    """
    if not sessions_dir.exists():
        return []
    cutoff = (now or datetime.now(UTC)).timestamp() - within_days * 86400
    results: list[tuple[float, Path, dict[str, Any], str]] = []
    for p in sessions_dir.rglob("*.md"):
        try:
            text = p.read_text()
        except OSError:
            continue
        fm = parse_frontmatter(text)
        if fm.get("scope") and fm.get("scope") != scope.scope:
            continue
        created = fm.get("created")
        if isinstance(created, str):
            try:
                ts = datetime.fromisoformat(created).timestamp()
            except ValueError:
                ts = p.stat().st_mtime
        elif hasattr(created, "timestamp"):
            ts = created.timestamp() if callable(created.timestamp) else p.stat().st_mtime
        else:
            ts = p.stat().st_mtime
        if ts < cutoff:
            continue
        preview = text[:800]
        results.append((ts, p, fm, preview))
    results.sort(key=lambda r: r[0], reverse=True)
    return [
        {"path": str(p), "frontmatter": fm, "preview": preview}
        for _, p, fm, preview in results[:limit]
    ]


def _merge_judgment(
    *,
    new_summary: NoteworthyResult,
    recent_notes: list[dict[str, Any]],
    anthropic_client,
    model_resolver: Callable[[str], str],
) -> dict[str, Any]:
    """Middle-tier call: {'merge': '<wikilink or path>'} or {'new': True}.

    If no recent_notes, short-circuits to `{'new': True}` — no LLM call.
    """
    if not recent_notes:
        return {"new": True}

    prompt = _merge_judgment_prompt(new_summary, recent_notes)
    tool = _merge_judgment_tool_schema(recent_notes)
    resp = anthropic_client.messages.create(
        model=model_resolver("middle"),
        max_tokens=512,
        tools=[tool],
        tool_choice={"type": "tool", "name": "merge_judgment"},
        messages=[{"role": "user", "content": prompt}],
    )
    for block in getattr(resp, "content", []):
        btype = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
        if btype == "tool_use":
            inp = getattr(block, "input", None)
            if inp is None and isinstance(block, dict):
                inp = block.get("input")
            if isinstance(inp, dict):
                return inp
    return {"new": True}


def _merge_judgment_prompt(new_summary: NoteworthyResult, recent_notes: list) -> str:
    lines = [
        "A new work slice has been captured. Decide whether it continues "
        "an existing session note in this scope or warrants a new note.",
        "",
        f"NEW SLICE:",
        f"  title: {new_summary.title}",
        f"  bullets: {new_summary.bullets}",
        f"  entities: {new_summary.entities}",
        f"  files_touched: {new_summary.files_touched}",
        "",
        "RECENT SESSION NOTES (last 7 days, same scope):",
    ]
    for i, n in enumerate(recent_notes):
        fm = n["frontmatter"]
        lines.append(f"  [{i}] path: {n['path']}")
        lines.append(f"      description: {fm.get('description', '(none)')}")
        lines.append(f"      created: {fm.get('created', '?')}")
    lines.append("")
    lines.append(
        "Call the `merge_judgment` tool. If it's a continuation of one of "
        "these, provide its path. Otherwise say new."
    )
    return "\n".join(lines)


def _merge_judgment_tool_schema(recent_notes: list) -> dict[str, Any]:
    return {
        "name": "merge_judgment",
        "description": "Decide whether the new slice merges into an existing note.",
        "input_schema": {
            "type": "object",
            "properties": {
                "merge": {
                    "type": "string",
                    "description": "Path of the note to merge into, or empty string if new.",
                },
                "new": {"type": "boolean"},
            },
        },
    }


_TRANSCRIPTS_CAP = 20


def _write_new_note(
    path: Path,
    *,
    scope: Scope,
    handle: TranscriptHandle,
    noteworthy: NoteworthyResult,
    turns: list[Turn],
    now: datetime,
    work_time: datetime,
    scope_redirected_from: str | None = None,
) -> None:
    from_hash = turns[0].content_hash() if turns else None
    to_hash = turns[-1].content_hash() if turns else None
    fm = {
        "schema_version": 2,
        "type": "session",
        "created": work_time.date().isoformat(),
        "last_reviewed": work_time.date().isoformat(),
        "description": noteworthy.title,
        "summary": noteworthy.summary or None,
        "scope": scope.scope,
        "draft": True,
        "curator_a_run": now.isoformat(),
        "source_transcripts": [
            {
                "host": handle.host,
                "id": handle.id,
                "from_hash": from_hash,
                "to_hash": to_hash,
            }
        ],
        "tags": [],
        "transcripts": [handle.id],
    }
    if scope_redirected_from:
        fm["scope_redirected_from"] = scope_redirected_from
    body = _render_body(noteworthy)
    text = _render_markdown(fm, body)
    atomic_write_text(path, text)


def _append_to_note(
    path: Path,
    *,
    noteworthy: NoteworthyResult,
    handle: TranscriptHandle,
    turns: list[Turn],
    now: datetime,
    work_time: datetime,
    scope_redirected_from: str | None = None,
) -> None:
    """Append a new section to an existing note; update frontmatter source_transcripts."""
    text = path.read_text()
    fm = parse_frontmatter(text)
    body = _strip_frontmatter(text)

    from_hash = turns[0].content_hash() if turns else None
    to_hash = turns[-1].content_hash() if turns else None
    src = fm.get("source_transcripts") or []
    src.append({
        "host": handle.host,
        "id": handle.id,
        "from_hash": from_hash,
        "to_hash": to_hash,
    })
    fm["source_transcripts"] = src
    fm["last_reviewed"] = work_time.date().isoformat()
    fm["curator_a_run"] = now.isoformat()
    if scope_redirected_from and "scope_redirected_from" not in fm:
        fm["scope_redirected_from"] = scope_redirected_from

    # P4b: append to the transcripts UUID list, dedupe, cap at 20 most
    # recent. Order is insertion-order with the newest at the end; if a
    # UUID is already present, it moves to the tail.
    existing = fm.get("transcripts") or []
    if not isinstance(existing, list):
        existing = []
    # Remove any prior occurrence so re-adding lands at the tail.
    uuid_list = [u for u in existing if u != handle.id]
    uuid_list.append(handle.id)
    if len(uuid_list) > _TRANSCRIPTS_CAP:
        uuid_list = uuid_list[-_TRANSCRIPTS_CAP:]
    fm["transcripts"] = uuid_list

    new_section = (
        f"\n\n## {noteworthy.title}\n\n"
        f"{_render_body(noteworthy)}"
    )
    text_new = _render_markdown(fm, body.rstrip() + new_section)
    atomic_write_text(path, text_new)


def _render_body(noteworthy: NoteworthyResult) -> str:
    lines = []
    if noteworthy.bullets:
        lines.append("### Summary")
        for b in noteworthy.bullets:
            lines.append(f"- {b}")
        lines.append("")
    if noteworthy.files_touched:
        lines.append("### Files touched")
        for f in noteworthy.files_touched:
            lines.append(f"- `{f}`")
        lines.append("")
    if noteworthy.decisions:
        lines.append("### Decisions")
        for d in noteworthy.decisions:
            lines.append(f"- {d}")
        lines.append("")
    if noteworthy.entities:
        links = ", ".join(f"[[{e}]]" for e in noteworthy.entities)
        lines.append(f"Entities: {links}")
    return "\n".join(lines).rstrip() + "\n"


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    return text[end + 4 :].lstrip("\n")


def _render_markdown(fm: dict[str, Any], body: str) -> str:
    dumped = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{dumped}\n---\n\n{body.rstrip()}\n"
