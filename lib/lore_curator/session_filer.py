"""Session-note writer (passive capture) — thin adapter over the shared writer.

Historical note: this module used to own the append-today merge rule,
frontmatter composition, and atomic write. Those responsibilities now
live in `lore_core.session_writer`; this module renders a
`NoteworthyResult` into a `SessionInput` and delegates. The public
entry point `file_session_note` keeps its signature so curator-A's
call site doesn't change.

Provenance contract
-------------------

Session notes carry a ``transcripts:`` list of source UUIDs (capped at
20 most-recent). The list is append-only: UUIDs must never be *moved*
between notes during merges, splits, or renames. They tag the note's
origin, not its ownership. `source_transcripts` adds from/to content
hashes for each append; it is NOT capped.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from lore_core.session_writer import FiledNote, SessionInput, file_or_merge
from lore_core.types import Scope, TranscriptHandle, Turn
from lore_curator.noteworthy import NoteworthyResult

if TYPE_CHECKING:
    from datetime import datetime

    from lore_core.run_log import RunLogger


__all__ = ["FiledNote", "file_session_note"]


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(title: str) -> str:
    """Lowercase, hyphen-separated, alphanumeric-only; trimmed. Max 60 chars."""
    s = _SLUG_RE.sub("-", title.lower()).strip("-")
    return s[:60] if s else "session"


def _resolve_handle_for(wiki_root: Path, handle: TranscriptHandle) -> str:
    """Return the canonical author handle for this transcript's cwd.

    Passive-capture doesn't carry the author identity on the transcript
    envelope; we resolve it lazily from the working repo's git config.
    Empty string in solo wikis is fine — the writer just skips sharding.
    """
    from lore_core.identity import resolve_handle

    try:
        import subprocess

        r = subprocess.run(
            ["git", "config", "user.email"],
            cwd=str(handle.cwd),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    email = r.stdout.strip() if r.returncode == 0 else ""
    return resolve_handle(wiki_root, email) if email else ""


def _render_body(noteworthy: NoteworthyResult) -> str:
    lines: list[str] = []
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


def file_session_note(
    *,
    scope: Scope,
    handle: TranscriptHandle,
    noteworthy: NoteworthyResult,
    turns: list[Turn],
    wiki_root: Path,
    now: "datetime | None" = None,
    work_time: "datetime | None" = None,
    logger: "RunLogger | None" = None,
    transcript_id: str | None = None,
    scope_redirected_from: str | None = None,
) -> FiledNote:
    """Passive-capture entry point. Synthesize SessionInput, delegate.

    `now` is *curation* time (when we looked). `work_time` is when the
    work in the turns actually happened — drives filename date and
    frontmatter `created` / `last_reviewed`. When omitted, falls back
    to `now`.
    """
    from datetime import UTC, datetime

    now = now or datetime.now(UTC)
    work_time = work_time or now

    from_hash = turns[0].content_hash() if turns else None
    to_hash = turns[-1].content_hash() if turns else None

    si = SessionInput(
        scope=scope,
        wiki_root=wiki_root,
        work_time=work_time,
        now=now,
        handle=_resolve_handle_for(wiki_root, handle),
        slug=_slug(noteworthy.title),
        description=noteworthy.title,
        summary=noteworthy.summary or "",
        body_markdown=_render_body(noteworthy),
        transcript=handle,
        turn_hashes=(from_hash, to_hash),
        scope_redirected_from=scope_redirected_from,
        # Phase C: structural file paths from this chunk's tool calls
        # (host-agnostic via ToolCall.category) drive topic-aware merge
        # decisions in session_writer. We trust the structural extraction
        # over noteworthy.files_touched (which the LLM can hallucinate).
        files_touched=_files_touched_from_turns(turns),
    )
    return file_or_merge(si, logger=logger, transcript_id=transcript_id)


# Each host names the file argument differently:
# - Claude Code:  Edit/Read/Write → ``file_path``
# - Cursor:       edit_file       → ``target_file``;  read_file → ``target_file``
# - VSCode/MCP:   applyEdit       → ``uri``;  many use generic ``path``
# - Older shapes: ``filename`` is occasionally seen in MCP server tools.
# Order matters — we return the first matching key — so prefer the most
# specific names first.
_FILE_PATH_INPUT_KEYS: tuple[str, ...] = (
    "file_path", "target_file", "path", "uri", "filename",
)


def _file_path_from_tool_input(inp: object) -> str | None:
    """Return the first non-empty string under any known file-path key."""
    if not isinstance(inp, dict):
        return None
    for key in _FILE_PATH_INPUT_KEYS:
        value = inp.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _files_touched_from_turns(turns: list[Turn]) -> list[str]:
    """Extract de-duplicated, ordered file paths from ``file_edit`` and
    ``file_read`` tool calls in the slice.

    Order is first-seen so frontmatter diffs stay readable; we don't sort.
    Uses canonical ToolCall.category so this works for any host whose
    adapter populates the field — Claude Code's Edit, Cursor's edit_file,
    Copilot's applyEdit all surface here uniformly. Each host names the
    path argument differently; :func:`_file_path_from_tool_input` walks
    a small list of known keys.
    """
    seen: set[str] = set()
    out: list[str] = []
    for t in turns:
        tc = t.tool_call
        if tc is None or tc.category not in ("file_edit", "file_read"):
            continue
        path = _file_path_from_tool_input(tc.input)
        if path and path not in seen:
            seen.add(path)
            out.append(path)
    return out
