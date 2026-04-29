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
from typing import TYPE_CHECKING, Any

from lore_core.session_writer import (
    BodySections,
    FiledNote,
    SessionInput,
    file_or_merge,
    render_body_sections,
)
from lore_core.types import Scope, TranscriptHandle, Turn
from lore_curator.noteworthy import NoteworthyResult
from lore_curator.session_activity import (
    collect_commits_in_window,
    collect_issues_in_window,
    collect_plans_advanced,
    collect_projects_for_session,
    extract_issue_refs,
    render_commits_section,
    render_issue_section,
)

if TYPE_CHECKING:
    from datetime import datetime

    from lore_core.run_log import RunLogger


__all__ = ["FiledNote", "file_session_note"]


_SLUG_RE = re.compile(r"[^a-z0-9]+")
_SLUG_MAX = 60


def _slug(title: str) -> str:
    """Lowercase, hyphen-separated, alphanumeric-only; smart-truncated.

    When the cleaned title exceeds ``_SLUG_MAX`` chars, truncate at the last
    hyphen boundary that keeps the slug within the limit so we never cut a
    word in half (the old hard ``[:60]`` produced filenames like
    "...rebase-onto-pha"). If no boundary fits — pathological case where the
    title is one giant unbroken alphanumeric blob — fall back to a hard cut.
    """
    s = _SLUG_RE.sub("-", title.lower()).strip("-")
    if not s:
        return "session"
    if len(s) <= _SLUG_MAX:
        return s
    truncated = s[:_SLUG_MAX]
    last_dash = truncated.rfind("-")
    if last_dash > 0:
        return truncated[:last_dash]
    return truncated


def _resolve_handle_for(wiki_root: Path, handle: TranscriptHandle) -> str:
    """Return the canonical author handle for this transcript's cwd.

    Passive-capture doesn't carry the author identity on the transcript
    envelope; we resolve it lazily from the working repo's git config.
    Empty string in solo wikis is fine — the writer just skips sharding.
    """
    from lore_core.identity import resolve_handle

    from lore_core.git import git_user_email

    email = git_user_email(handle.cwd, env_override=None)
    return resolve_handle(wiki_root, email) if email else ""


def _sections_from_noteworthy(
    noteworthy: NoteworthyResult,
    *,
    commits: list[str] | None = None,
    issues_opened: list[str] | None = None,
    issues_closed: list[str] | None = None,
) -> BodySections:
    """Project Curator A's output into the locked body-section shape.

    ``files_touched`` and ``entities`` are dropped: the former lives in
    frontmatter (no need to duplicate in body) and the latter had no
    consistent contract (mix of file basenames, branch names, version
    strings, concept names — useful for none of them).

    Phase 3: Activity sub-section bullets (commits / issues opened /
    issues closed) flow through as pre-rendered ``- ...`` lines from
    the mechanical collectors.
    """
    decisions = [f"- {d}" for d in noteworthy.decisions if d]
    worked_on = [f"- {b}" for b in noteworthy.bullets if b]
    loose_ends = [f"- {le}" for le in noteworthy.loose_ends if le]
    return BodySections(
        title=noteworthy.title or "session",
        summary=noteworthy.description or "",
        decisions=decisions,
        worked_on=worked_on,
        loose_ends=loose_ends,
        commits=list(commits or []),
        issues_opened=list(issues_opened or []),
        issues_closed=list(issues_closed or []),
    )


def _render_body(
    noteworthy: NoteworthyResult,
    *,
    commits: list[str] | None = None,
    issues_opened: list[str] | None = None,
    issues_closed: list[str] | None = None,
) -> str:
    """Render a fresh chunk's body in the locked layout.

    Append-mode (``session_writer._append_to_note``) re-parses this output
    and merges it into the existing note's sections rather than wrapping
    it in a per-chunk H2.
    """
    return render_body_sections(_sections_from_noteworthy(
        noteworthy,
        commits=commits, issues_opened=issues_opened, issues_closed=issues_closed,
    ))


def _turn_window(turns: list[Turn], *, fallback: datetime) -> tuple[datetime, datetime]:
    """Return (since, until) for the chunk's git/gh queries.

    Uses the earliest and latest timestamped turns; falls back to the
    caller-supplied work_time when no turn carries a timestamp (the
    fallback path during deterministic tests).
    """
    times = [t.timestamp for t in turns if t.timestamp is not None]
    if not times:
        return fallback, fallback
    return min(times), max(times)


def _all_turn_text(turns: list[Turn]) -> str:
    """Concatenate the user/assistant text content of a chunk's turns.

    Used for free-text issue-reference extraction (``opened #42`` /
    ``closes #29``). Tool-result text is intentionally skipped — it's
    high-volume and rarely contains genuine issue actions; including
    it would add false-positives without much recall.
    """
    parts: list[str] = []
    for t in turns:
        if t.text:
            parts.append(t.text)
    return "\n".join(parts)


def _collect_activity(
    *,
    cwd: Path,
    wiki_root: Path,
    turns: list[Turn],
    files_touched: list[str],
    body_text_for_plan_scan: str,
    fallback_time: datetime,
) -> dict[str, Any]:
    """Run all Phase-3 collectors for a chunk and return the inputs the
    body renderer + frontmatter need.

    Returns a dict with keys ``commits``, ``issues_opened``,
    ``issues_closed`` (rendered bullet lines), ``plans``, ``projects``
    (ref strings).
    """
    from lore_core.git import git_repo_root, current_repo

    repo_root = git_repo_root(cwd)
    repo = current_repo(cwd) or ""
    since, until = _turn_window(turns, fallback=fallback_time)

    raw_commits = collect_commits_in_window(repo_root, since=since, until=until)

    # Issue-reference extraction: union turn text + commit subjects so
    # `closes #29` lands whether the LLM wrote it in chat or only in a
    # commit message.
    commit_text = "\n".join(c.subject for c in raw_commits)
    turn_text = _all_turn_text(turns)
    opened_refs, closed_refs = extract_issue_refs(turn_text + "\n" + commit_text)

    issues_opened, issues_closed = collect_issues_in_window(
        repo,
        referenced_opened=opened_refs,
        referenced_closed=closed_refs,
    )

    plans = collect_plans_advanced(
        repo_root=repo_root,
        body_text=body_text_for_plan_scan,
        wiki_root=wiki_root,
        since=since,
        until=until,
    )
    projects = collect_projects_for_session(
        cwd=cwd,
        files_touched=files_touched,
        wiki_root=wiki_root,
    )

    return {
        "commits": render_commits_section(raw_commits),
        "issues_opened": render_issue_section(issues_opened, repo=repo),
        "issues_closed": render_issue_section(issues_closed, repo=repo),
        "plans": plans,
        "projects": projects,
    }


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

    files_touched = _files_touched_from_turns(turns)

    # Plan-wikilink scan looks at the noteworthy body content (bullets +
    # decisions + loose ends + description) rather than the rendered
    # markdown — same source material, no parser round-trip.
    plan_scan_text = "\n".join([
        noteworthy.description or "",
        *noteworthy.bullets,
        *noteworthy.decisions,
        *noteworthy.loose_ends,
    ])

    activity = _collect_activity(
        cwd=handle.cwd,
        wiki_root=wiki_root,
        turns=turns,
        files_touched=files_touched,
        body_text_for_plan_scan=plan_scan_text,
        fallback_time=work_time,
    )

    # Provenance — record which LLM produced this note's verdict so model
    # swaps (subscription → OpenAI-compatible → SDK) can be diagnosed
    # retroactively. Empty strings are skipped so cascade_trivial and
    # legacy fixtures don't add noisy keys.
    extra_fm: dict[str, Any] = {}
    if noteworthy.llm_backend:
        extra_fm["llm_backend"] = noteworthy.llm_backend
    if noteworthy.llm_model:
        extra_fm["llm_model"] = noteworthy.llm_model

    si = SessionInput(
        scope=scope,
        wiki_root=wiki_root,
        work_time=work_time,
        now=now,
        handle=_resolve_handle_for(wiki_root, handle),
        slug=_slug(noteworthy.title),
        # New shape: ``title`` is the content-named slug-source / body H1.
        # ``description`` is the 1-2-sentence status-line preview that used to
        # live in the dropped ``summary`` field. Falls back to title for the
        # cascade-trivial path, which only emits a title.
        title=noteworthy.title,
        description=noteworthy.description or noteworthy.title,
        body_markdown=_render_body(
            noteworthy,
            commits=activity["commits"],
            issues_opened=activity["issues_opened"],
            issues_closed=activity["issues_closed"],
        ),
        transcript=handle,
        turn_hashes=(from_hash, to_hash),
        scope_redirected_from=scope_redirected_from,
        # Phase C: structural file paths from this chunk's tool calls
        # (host-agnostic via ToolCall.category) drive topic-aware merge
        # decisions in session_writer. We trust the structural extraction
        # over noteworthy.files_touched (which the LLM can hallucinate).
        files_touched=files_touched,
        # Phase 3: cross-note linkage (frontmatter) + Activity bullets
        # (already rendered into body_markdown above; copies live here so
        # append-mode can re-derive without re-running collectors).
        plans=activity["plans"],
        projects=activity["projects"],
        activity_commits=activity["commits"],
        activity_issues_opened=activity["issues_opened"],
        activity_issues_closed=activity["issues_closed"],
        extra_frontmatter=extra_fm,
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
