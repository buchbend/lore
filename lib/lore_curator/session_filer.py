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
from lore_curator.llm_client import LlmClient
from lore_curator.noteworthy import NoteworthyResult
# Turn-deterministic activity extraction lives in session_activity. The
# leading-underscore re-exports preserve the legacy import paths used
# throughout the test suite; new code (buffer_append, stub_note) should
# import from session_activity directly.
from lore_curator.session_activity import (
    _COMMIT_SHA_LINE_RE,  # noqa: F401  (test back-compat)
    _FILE_PATH_INPUT_KEYS,  # noqa: F401  (test back-compat)
    _all_turn_text,  # noqa: F401  (test back-compat)
    _collect_activity,
    _commit_shas_from_bash_results,  # noqa: F401  (test back-compat)
    _file_path_from_tool_input,  # noqa: F401  (test back-compat)
    _files_touched_from_turns,
    _is_git_commit_command,  # noqa: F401  (test back-compat)
    collect_commits_by_sha,  # noqa: F401  (monkeypatch surface for test_session_filer)
    collect_issues_in_window,  # noqa: F401  (monkeypatch surface for test_session_filer)
)
from lore_curator.summary_merge import merge_descriptions

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
    llm_client: LlmClient | None = None,
    summary_merge_model: str | None = None,
) -> FiledNote:
    """Passive-capture entry point. Synthesize SessionInput, delegate.

    `now` is *curation* time (when we looked). `work_time` is when the
    work in the turns actually happened — drives filename date and
    frontmatter `created` / `last_reviewed`. When omitted, falls back
    to `now`.

    ``llm_client`` + ``summary_merge_model`` enable Curator A's
    LLM-merged summary on append: when this filing ends up appending to
    an existing same-day note, the writer's merger closure asks the LLM
    to compose a merged summary that anchors on the existing framing
    and works the new chunk's context in (rather than clobbering or
    going sticky). When either is omitted, the writer falls back to its
    deterministic sticky-existing rule — fine for tests, dry-runs, and
    the explicit /lore:session path.
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
        logger=logger,
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

    summary_merger = _make_summary_merger(
        llm_client=llm_client,
        model=summary_merge_model,
        logger=logger,
        transcript_id=transcript_id,
    )

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
        summary_merger=summary_merger,
    )
    return file_or_merge(si, logger=logger, transcript_id=transcript_id)


def _make_summary_merger(
    *,
    llm_client: LlmClient | None,
    model: str | None,
    logger: "RunLogger | None",
    transcript_id: str | None,
):
    """Build the closure the writer invokes when an append happens.

    Returns ``None`` when LLM merge isn't available — the writer's
    sticky-existing fallback handles that case. Otherwise returns a
    callable matching ``SessionWriter.SummaryMerger`` that delegates
    to :func:`merge_descriptions`.
    """
    if llm_client is None or not model:
        return None

    def _merger(
        existing_summary: str,
        new_summary: str,
        new_worked_on: list[str],
        new_decisions: list[str],
    ) -> str:
        return merge_descriptions(
            existing=existing_summary,
            new=new_summary,
            new_bullets=new_worked_on,
            new_decisions=new_decisions,
            llm_client=llm_client,
            model=model,
            logger=logger,
            transcript_id=transcript_id,
        )

    return _merger


# Turn-deterministic helpers (``_files_touched_from_turns``,
# ``_commit_shas_from_bash_results``, ``_collect_activity`` and the
# regex/keys/parser primitives behind them) live in
# ``lore_curator.session_activity`` so the buffer-and-flush heartbeat
# path can call them without dragging the LLM-summary-merge surface
# along. Imports above re-export them under the legacy underscore names
# for tests that pre-date the move.
