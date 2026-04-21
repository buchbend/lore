"""Session-note writer / merger — create or merge into an existing note.

Given a `NoteworthyResult` + full `Turn` slice + existing recent session
notes in scope, creates a new session note or merges into an existing one.
Uses session-note-schema-v2 frontmatter. `draft: true`. Records source
transcripts with hash watermarks.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
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
    wikilink: str                   # e.g. "[[2026-04-19-add-ledger]]"
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
    sessions_dir.mkdir(parents=True, exist_ok=True)

    recent_notes = _recent_session_notes(sessions_dir, scope=scope, within_days=7, limit=20)
    decision = _merge_judgment(
        new_summary=noteworthy,
        recent_notes=recent_notes,
        anthropic_client=anthropic_client,
        model_resolver=model_resolver,
    )

    if decision.get("merge"):
        target = Path(decision["merge"])
        if not target.is_absolute():
            target = sessions_dir / target
        target_slug = target.stem

        if logger is not None:
            logger.emit(
                "merge-check",
                transcript_id=transcript_id,
                target=f"[[{target_slug}]]",
                similarity=None,
                decision="merge",
            )

        _append_to_note(
            target,
            noteworthy=noteworthy,
            handle=handle,
            turns=turns,
            now=now,
            work_time=work_time,
        )
        wikilink = _wikilink_for(target)

        if logger is not None:
            logger.emit(
                "session-note",
                transcript_id=transcript_id,
                action="merged",
                wikilink=wikilink,
            )

        return FiledNote(path=target, wikilink=wikilink, was_merge=True)

    # New note
    slug = _slug(noteworthy.title)
    date_str = work_time.date().isoformat()
    path = sessions_dir / f"{date_str}-{slug}.md"
    # Avoid collisions — append counter if needed
    counter = 1
    while path.exists():
        counter += 1
        path = sessions_dir / f"{date_str}-{slug}-{counter}.md"

    if logger is not None and recent_notes:
        logger.emit(
            "merge-check",
            transcript_id=transcript_id,
            target=None,
            similarity=None,
            decision="new",
        )

    _write_new_note(
        path,
        scope=scope,
        handle=handle,
        noteworthy=noteworthy,
        turns=turns,
        now=now,
        work_time=work_time,
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


def _wikilink_for(path: Path) -> str:
    return f"[[{path.stem}]]"


def _recent_session_notes(
    sessions_dir: Path,
    *,
    scope: Scope,
    within_days: int,
    limit: int,
) -> list[dict[str, Any]]:
    """Return list of {path, frontmatter, preview} for recent notes in scope.

    Sorted newest first. `within_days` filters by `created` in frontmatter
    (falls back to mtime if missing).
    """
    if not sessions_dir.exists():
        return []
    cutoff = datetime.now(UTC).timestamp() - within_days * 86400
    results: list[tuple[float, Path, dict[str, Any], str]] = []
    for p in sessions_dir.glob("*.md"):
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


def _write_new_note(
    path: Path,
    *,
    scope: Scope,
    handle: TranscriptHandle,
    noteworthy: NoteworthyResult,
    turns: list[Turn],
    now: datetime,
    work_time: datetime,
) -> None:
    from_hash = turns[0].content_hash() if turns else None
    to_hash = turns[-1].content_hash() if turns else None
    fm = {
        "schema_version": 2,
        "type": "session",
        "created": work_time.date().isoformat(),
        "last_reviewed": work_time.date().isoformat(),
        "description": noteworthy.title,
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
    }
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
) -> None:
    """Append a new section to an existing note; update frontmatter source_transcripts.

    Frontmatter updates:
      - last_reviewed → work_time (when the newly-added work happened)
      - curator_a_run → now ISO (audit field: when WE looked)
      - source_transcripts gets a new entry
    Body: append a `## <noteworthy.title>` section with bullets + findings.
    """
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
