"""Two-phase flush worker for the buffer-and-flush curator.

Phase 1 — deterministic, never raises:

1. Acquire the per-buffer flock; CAS ``ready -> flushing``. If the
   buffer is already ``flushing`` / ``closed``, return idempotently.
2. Replay the JSONL log into final accumulators
   (files_touched / plans / projects / Activity bullets / slice
   pointers).
3. Validate plan / project refs against the vault; drop dangles, emit
   ``dangling-ref`` telemetry.
4. Re-render :class:`BodySections` with Activity from accumulators;
   ``summary`` / ``decisions`` / ``worked_on`` / ``loose_ends`` stay
   empty.
5. Atomically rewrite the stub at ``sidecar.stub_path``; drop
   frontmatter ``state: stub``; for Part >= 2 add ``part: N`` and
   ``continues: [[<prev>]]``.
6. CAS ``flushing -> closed`` — handover unblocks here.
7. Move sidecar+log to ``.lore/buffers/_done/``.

The Phase-1 note is already useful by itself — Activity (commits /
issues / files) is the most evidentiary part of any session note.

Phase 2 — LLM composition, optional, in the same worker:

1. One LLM tool-use call composes ``{title, summary, description,
   decisions[], worked_on[], loose_ends[]}`` from the full slice the
   buffer pointed at.
2. For Part >= 2, the prompt tells the model "this continues
   [[<prev>]]; do not re-summarise, focus on this part's arc".
3. Re-open the finalised stub, rewrite frontmatter ``title`` /
   ``description`` and body sections (``# H1``, ``## Summary``,
   ``## Decisions made``, ``## What we worked on``, ``## Loose ends``).
   Activity / files_touched / plans / projects / provenance preserved.
4. Bullet caps enforced post-LLM: ``decisions`` <= 5, ``worked_on``
   <= 8, ``loose_ends`` <= 5; each line <= 120 chars.
5. On failure: ``flush_attempts++``, retry up to 3 times in-process.
   On exhaustion, emit ``flush-degraded`` and leave the Activity-only
   note intact. **No state rollback** — buffer stays ``closed``.
6. For Part >= 2, back-fill ``continued_by: [[<this>]]`` onto the prior
   part's frontmatter.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from lore_adapters import Adapter, get_adapter
from lore_core.io import atomic_write_text
from lore_core.narrative_kind import NarrativeShape, select_shape
from lore_core.schema import parse_frontmatter, strip_frontmatter
from lore_core.session_writer import (
    BodySections,
    FiledNote,
    render_body_sections,
)
from lore_core.types import TranscriptHandle, Turn
from lore_core.wikilinks import sanitize_for_write
from lore_curator._auto_commit import maybe_auto_commit
from lore_curator.buffer_store import (
    Buffer,
    ReplayedBuffer,
    Sidecar,
    _now_iso,
    done_dir,
)
from lore_curator.session_activity import _files_modified_from_turns
from lore_curator.session_filer import _slug
from lore_curator.stub_note import (
    STUB_DESCRIPTION_PLACEHOLDER,
    STUB_FRONTMATTER_STATE,
    STUB_SUMMARY_PLACEHOLDER,
    file_lists_for_frontmatter,
)

if TYPE_CHECKING:
    from lore_core.run_log import RunLogger


__all__ = [
    "FlushOutcome",
    "compose_session_note",
    "flush_buffer",
    "synth_in_place",
    "synth_and_close",
    "spawn_detached_flush",
    "BULLET_CAPS",
    "BULLET_LINE_MAX",
]


# Phase-2 caps. Phrased as a tool-schema constraint AND post-validated.
BULLET_CAPS = {
    "decisions": 5,
    "worked_on": 8,
    "loose_ends": 5,
    "discussion": 8,  # mirrors worked_on cap; discussion replaces it in non-work shape
}


# Title-verb gate (step-6 of yes-do-that-keen-yeti). In discussion shape
# the title MUST NOT lead with a verb that promises work the session
# didn't deliver. The Phase-2 prompt steers the LLM toward
# "Discussed:" / "Explored:" / "Sketched:" / "Reviewed:" or a noun-
# phrase title; this set + the coercion below enforce it deterministically.
_DELIVERABLE_VERBS = frozenset({
    "refactor", "refactored", "refactoring",
    "add", "added", "adding",
    "fix", "fixed", "fixing",
    "implement", "implemented", "implementing",
    "migrate", "migrated", "migrating",
    "build", "built", "building",
    "ship", "shipped", "shipping",
    "create", "created", "creating",
    "delete", "deleted", "deleting",
    "remove", "removed", "removing",
    "update", "updated", "updating",
    "replace", "replaced", "replacing",
    "land", "landed", "landing",
    "rewrite", "rewrote", "rewriting",
})


_DISCUSSION_LEADS = frozenset({
    "discussed", "discussed:",
    "explored", "explored:",
    "sketched", "sketched:",
    "reviewed", "reviewed:",
    "considered", "considered:",
    "drafted", "drafted:",
    "proposed", "proposed:",
})


_TITLE_WORD_CAP = 8
BULLET_LINE_MAX = 280
PHASE2_MAX_ATTEMPTS = 3
PHASE2_MAX_OUTPUT_TOKENS = 1024


# ---------------------------------------------------------------------------
# Outcome
# ---------------------------------------------------------------------------


@dataclass
class FlushOutcome:
    buffer_stem: str
    state_before: str = ""
    phase1_completed: bool = False
    phase2_completed: bool = False
    phase2_attempts: int = 0
    degraded: bool = False
    skipped_reason: str = ""
    stub_path: Path | None = None
    wikilink: str = ""
    composed: dict[str, Any] | None = None
    dangling_projects: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers (frontmatter + body)
# ---------------------------------------------------------------------------


def _render_markdown(fm: dict[str, Any], body: str, *, wiki_root: Path) -> str:
    dumped = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    text = f"---\n{dumped}\n---\n\n{body.rstrip()}\n"
    return sanitize_for_write(text, wiki_root)


def _validate_refs(
    refs: list[str],
    *,
    subdir: Path,
) -> tuple[list[str], list[str]]:
    """Split ``refs`` into (valid, dangling) by checking ``<subdir>/<ref>.md``.

    ``ref`` may be a bare slug or ``<slug>#sN`` — strip the ``#sN`` for the
    file existence check.
    """
    valid: list[str] = []
    dangling: list[str] = []
    if not subdir.exists():
        return [], list(refs)
    for ref in refs:
        slug = ref.split("#", 1)[0].strip()
        if not slug:
            continue
        if (subdir / f"{slug}.md").exists():
            valid.append(ref)
        else:
            dangling.append(ref)
    return valid, dangling


def _strip_dangles(
    rb: ReplayedBuffer,
    *,
    wiki_root: Path,
    logger: "RunLogger | None",
    transcript_id: str,
) -> tuple[list[str], list[str]]:
    """Return ``(valid_projects, dangling_projects)``.

    Emits ``dangling-ref`` telemetry per dropped ref.
    """
    projects, dangling_projects = _validate_refs(rb.projects, subdir=wiki_root / "projects")
    if logger is not None:
        for d in dangling_projects:
            logger.emit("dangling-ref", kind="project", ref=d, transcript_id=transcript_id)
    return projects, dangling_projects


# ---------------------------------------------------------------------------
# Phase 1 — deterministic finalise
# ---------------------------------------------------------------------------


def _phase1_finalise(
    *,
    buffer: Buffer,
    sidecar: Sidecar,
    rb: ReplayedBuffer,
    wiki_root: Path,
    logger: "RunLogger | None",
    in_place: bool = False,
) -> FlushOutcome:
    """Run Phase 1 (deterministic) over ``buffer``'s replayed state.

    ``in_place`` toggles between two behaviours:

    - ``False`` (default — ``synth_and_close``): drops the
      ``state: stub`` frontmatter marker so the handover treats the note
      as filed. Used by cap-trip / reaper paths that will close the
      buffer immediately after.
    - ``True`` (``synth_in_place``): retains ``state: stub`` because the
      buffer remains live and may receive more chunks. The merge gate
      and stub-protection branch read ``state: stub`` to mean "buffer
      is live"; popping it under in-place would orphan that semantic.
    """
    out = FlushOutcome(
        buffer_stem=buffer.stem,
        state_before=sidecar.state,
    )

    projects, dpr = _strip_dangles(
        rb, wiki_root=wiki_root,
        logger=logger, transcript_id=sidecar.transcript_id,
    )
    out.dangling_projects = dpr

    stub_path = Path(sidecar.stub_path) if sidecar.stub_path else None
    if stub_path is None or not stub_path.exists():
        # No stub on disk — we can still close the buffer cleanly; the
        # handover-poller will find ``state=closed`` and inject "previous
        # session being synthesised" text. Telemetry flags this rare case.
        if logger is not None:
            logger.emit(
                "flush-deterministic-completed",
                transcript_id=sidecar.transcript_id,
                buffer_stem=buffer.stem,
                stub_present=False,
                in_place=in_place,
            )
        return out

    text = stub_path.read_text()
    fm = parse_frontmatter(text)
    body_text = strip_frontmatter(text)  # noqa: F841 - body is re-rendered

    # Stub marker semantics:
    # - synth_and_close (in_place=False): drop the marker — the note is
    #   final from the merge gate's POV.
    # - synth_in_place (in_place=True): keep the marker — buffer is
    #   still alive and may absorb more chunks before close.
    if in_place:
        fm.setdefault("state", STUB_FRONTMATTER_STATE)
    else:
        fm.pop("state", None)
    fm["last_reviewed"] = datetime.now(UTC).date().isoformat()
    fm["curator_a_run"] = datetime.now(UTC).isoformat()
    fm.setdefault("title", fm.get("title", "session"))
    fm.setdefault("description", fm.get("description", STUB_DESCRIPTION_PLACEHOLDER))
    fm.setdefault("type", "session")
    fm.setdefault("schema_version", 2)
    fm.setdefault("scope", sidecar.scope)
    # New schema: ``files_modified`` (edits only — load-bearing for the
    # merge-gate Jaccard, retrieval, and narrative tense) + ``files_read``
    # (reads not subsumed by edits — kept for interview / code-tour
    # provenance). ``files_touched`` is no longer written by any new path;
    # we pop any stale value the stub carried so the new shape is the
    # single source of truth on this curator-owned note.
    fm.pop("files_touched", None)
    fm.update(file_lists_for_frontmatter(rb.files_modified, rb.files_read))
    if projects:
        fm["projects"] = projects
    elif "projects" in fm and dpr:
        fm.pop("projects", None)
    fm.pop("plans", None)
    if sidecar.part_index >= 2:
        fm["part"] = sidecar.part_index
        if sidecar.continuation_of:
            fm["continues"] = f"[[{sidecar.continuation_of}]]"
    fm["buffer_stem"] = buffer.stem

    body = render_body_sections(BodySections(
        title=fm.get("title", "session"),
        summary=fm.get("description", STUB_SUMMARY_PLACEHOLDER) or STUB_SUMMARY_PLACEHOLDER,
        decisions=[],
        worked_on=[],
        loose_ends=[],
        commits=rb.activity_commits,
        issues_opened=rb.activity_issues_opened,
        issues_closed=rb.activity_issues_closed,
    ))
    new_text = _render_markdown(fm, body, wiki_root=wiki_root)
    atomic_write_text(stub_path, new_text)

    out.phase1_completed = True
    out.stub_path = stub_path
    out.wikilink = f"[[{stub_path.stem}]]"

    # Part >= 2: back-fill ``continued_by`` on the prior part's note.
    if sidecar.part_index >= 2 and sidecar.continuation_of:
        _backfill_continued_by(
            buffer=buffer,
            wiki_root=wiki_root,
            sidecar=sidecar,
            this_stub_path=stub_path,
            logger=logger,
        )

    if logger is not None:
        logger.emit(
            "flush-deterministic-completed",
            transcript_id=sidecar.transcript_id,
            buffer_stem=buffer.stem,
            stub_path=str(stub_path),
            wikilink=out.wikilink,
            files_modified_count=len(rb.files_modified),
            files_read_count=len(rb.files_read),
            commit_count=len(rb.activity_commits),
        )
    return out


def _backfill_continued_by(
    *,
    buffer: Buffer,
    wiki_root: Path,
    sidecar: Sidecar,
    this_stub_path: Path,
    logger: "RunLogger | None",
) -> None:
    """Patch the prior part's frontmatter with ``continued_by: [[<this>]]``.

    Reads the prior buffer's sidecar (live or _done) to find its
    ``stub_path``. Best-effort — emits a warning on failure but never
    raises (this is a polish field, not a correctness invariant).
    """
    prev_stem = sidecar.continuation_of
    if not prev_stem:
        return
    prior = _find_buffer_sidecar(buffer.lore_root, prev_stem)
    if prior is None or not prior.stub_path:
        return
    prior_path = Path(prior.stub_path)
    if not prior_path.exists():
        return
    try:
        text = prior_path.read_text()
        fm = parse_frontmatter(text)
        fm["continued_by"] = f"[[{this_stub_path.stem}]]"
        body = strip_frontmatter(text)
        new_text = _render_markdown(fm, body, wiki_root=wiki_root)
        atomic_write_text(prior_path, new_text)
    except OSError as exc:
        if logger is not None:
            logger.emit(
                "warning",
                call="continued-by-backfill",
                message=f"failed to patch {prior_path}: {exc}",
            )


def _find_buffer_sidecar(lore_root: Path, stem: str) -> Sidecar | None:
    """Look for ``<stem>.state.json`` in live and ``_done/`` dirs."""
    base = lore_root / ".lore" / "buffers"
    for candidate in (base / f"{stem}.state.json", base / "_done" / f"{stem}.state.json"):
        if not candidate.exists():
            continue
        try:
            raw = json.loads(candidate.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(raw, dict):
            return Sidecar.from_dict(raw)
    return None


# ---------------------------------------------------------------------------
# Phase 2 — LLM compose
# ---------------------------------------------------------------------------


def compose_session_note(
    *,
    turns_text: str,
    activity_summary: str,
    is_continuation: bool,
    continues_wikilink: str | None,
    llm_client: Any,
    model: str,
    logger: "RunLogger | None" = None,
    transcript_id: str = "",
    shape: NarrativeShape | None = None,
) -> dict[str, Any] | None:
    """One-shot composition: ``turns_text`` -> ``{title, summary, ...}``.

    Returns ``None`` on any failure (LLM exception, malformed tool_use,
    empty output). The caller increments ``flush_attempts`` and retries
    up to :data:`PHASE2_MAX_ATTEMPTS`.

    ``shape`` (added in step-3 of the conditional-Decisions plan) is used
    by step-4 to gate the Phase-2 schema and prompt. ``None`` preserves
    the existing work-shape behaviour for tests and callers that haven't
    been migrated.
    """
    prompt = _phase2_prompt(
        turns_text=turns_text,
        activity_summary=activity_summary,
        is_continuation=is_continuation,
        continues_wikilink=continues_wikilink,
        shape=shape,
    )
    schema = _phase2_tool_schema(shape)
    if logger is not None:
        logger.emit(
            "llm-prompt",
            call="compose-session-note",
            transcript_id=transcript_id,
            prompt_chars=len(prompt),
            turns_text_chars=len(turns_text),
            activity_summary_chars=len(activity_summary),
        )
    t0 = time.monotonic()
    try:
        resp = llm_client.messages.create(
            model=model,
            max_tokens=PHASE2_MAX_OUTPUT_TOKENS,
            tools=[schema],
            tool_choice={"type": "tool", "name": "compose"},
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:  # noqa: BLE001 - any exc -> caller retries
        if logger is not None:
            logger.emit(
                "warning",
                call="compose-session-note",
                message=f"LLM call raised: {type(exc).__name__}: {exc}",
            )
        return None
    latency_ms = int((time.monotonic() - t0) * 1000)
    data = _extract_tool_input(resp)
    if not isinstance(data, dict):
        if logger is not None:
            logger.emit(
                "warning",
                call="compose-session-note",
                message="no tool_use block in response",
            )
        return None
    # Defensive: even with ``additionalProperties: false`` set, the
    # Anthropic SDK does not validate tool_use responses against the
    # schema we sent. Strip any keys that aren't in the schema we built
    # for this shape and log them — an LLM emitting ``decisions[]`` in
    # discussion shape would otherwise silently slip past the gate.
    allowed_keys = set(schema["input_schema"]["properties"].keys())
    extra_keys = sorted(k for k in data.keys() if k not in allowed_keys)
    if extra_keys and logger is not None:
        logger.emit(
            "warning",
            call="compose-extra-key",
            transcript_id=transcript_id,
            shape_kind=shape.kind if shape is not None else "unspecified",
            extra_keys=extra_keys,
        )
    data = {k: v for k, v in data.items() if k in allowed_keys}
    # Title-verb coercion: in discussion shape, strip leading deliverable
    # verbs and prepend ``Discussed:``. Pure post-LLM safety net for the
    # cases the prompt didn't catch.
    title_in = data.get("title", "")
    title_out = _coerce_title_for_shape(title_in, shape)
    if title_out != title_in:
        data["title"] = title_out
        if logger is not None:
            logger.emit(
                "warning",
                call="compose-title-coerced",
                transcript_id=transcript_id,
                shape_kind=shape.kind if shape is not None else "unspecified",
                title_in=title_in,
                title_out=title_out,
            )
    if logger is not None:
        logger.emit(
            "llm-response",
            call="compose-session-note",
            transcript_id=transcript_id,
            latency_ms=latency_ms,
            keys=sorted(data.keys()),
        )
    return data


def _coerce_title_for_shape(title: str, shape: NarrativeShape | None) -> str:
    """Enforce the no-deliverable-verb rule for discussion-shape titles.

    Work shape: untouched. Discussion shape: if the title leads with a
    deliverable verb (``Refactor``, ``Add``, ``Fix``, …) the verb is
    stripped and ``Discussed:`` is prepended. Already-discussion-led
    titles (``Discussed:``, ``Explored:``, ``Sketched:``, ``Reviewed:``,
    …) and noun-phrase titles pass through unchanged.

    The 6-8 word cap is enforced by truncating trailing words rather
    than the prefix — the rewritten title's framing is more important
    than its tail.
    """
    if shape is None or shape.has_edits:
        return title
    title = title.strip()
    if not title:
        return title
    words = title.split()
    if not words:
        return title
    first_norm = words[0].lower().rstrip(":,")
    if first_norm in _DISCUSSION_LEADS:
        # Already discussion-shaped (or has a recognised lead). Honour
        # the model's framing and just enforce the word cap.
        return _truncate_title_words(title)
    if first_norm not in _DELIVERABLE_VERBS:
        # Not a deliverable verb — could be a noun phrase or a less-
        # suspect verb. Leave alone; the prompt does most of the
        # steering, and over-coercing would replace legitimate framings
        # with a generic ``Discussed:`` prefix.
        return _truncate_title_words(title)
    # Strip the deliverable verb and prepend ``Discussed:``. If the
    # remainder is empty (one-word title like "Refactor"), fall back to
    # a placeholder so the slug isn't degenerate.
    rest = " ".join(words[1:]).strip()
    coerced = f"Discussed: {rest}" if rest else "Discussed: session"
    return _truncate_title_words(coerced)


def _truncate_title_words(title: str, cap: int = _TITLE_WORD_CAP) -> str:
    """Drop trailing words past ``cap``. Keeps the leading ``Discussed:``
    style prefix so the framing of the title is preserved at the cost
    of the tail."""
    words = title.split()
    if len(words) <= cap:
        return title
    return " ".join(words[:cap])


def _phase2_prompt(
    *,
    turns_text: str,
    activity_summary: str,
    is_continuation: bool,
    continues_wikilink: str | None,
    shape: NarrativeShape | None = None,
) -> str:
    is_discussion = shape is not None and not shape.has_edits
    if is_discussion:
        bullet_line = (
            f"Bullet-count caps are hard: discussion <= "
            f"{BULLET_CAPS.get('discussion', 8)}, loose_ends <= "
            f"{BULLET_CAPS['loose_ends']}. Each bullet line <= "
            f"{BULLET_LINE_MAX} chars."
        )
    else:
        bullet_line = (
            f"Bullet-count caps are hard: decisions <= {BULLET_CAPS['decisions']}, "
            f"worked_on <= {BULLET_CAPS['worked_on']}, loose_ends <= "
            f"{BULLET_CAPS['loose_ends']}. Each bullet line <= "
            f"{BULLET_LINE_MAX} chars."
        )

    parts: list[str] = [
        "You are composing the human-readable narrative for ONE session "
        "note. The deterministic Activity (commits, issues, files touched) "
        "is already in place; your job is to write the narrative the reader "
        "sees first.",
        "",
        f"Return your output via the `compose` tool. {bullet_line}",
        "",
        "Title: 6-8 words; content-named (NOT phase numbers or release "
        "labels); reads as a filename a year from now.",
        "Description: 1-2 sentences; what + why in one breath.",
        "Summary: 4-5 sentence body paragraph; substance, not mechanics.",
        "Bullets: lead with a 2-5 word bold phrase, colon, then detail.",
        "Loose ends: past-tense / stative phrasing, never imperatives.",
        "",
        "Wikilink discipline: `[[ ]]` is reserved for vault note slugs that "
        "actually exist. Use backticks for code-shaped tokens, plain text "
        "otherwise.",
    ]

    # Shape-specific clause. The schema has already been narrowed for
    # this shape (see ``_phase2_tool_schema``); the prompt makes the
    # narrowing explicit so the model doesn't waste output budget
    # trying to emit fields that aren't in its tool spec.
    if is_discussion:
        intent_note = ""
        if shape is not None and shape.no_edit_intent:
            intent_note = (
                " The user explicitly disclaimed intent to change code "
                "('no code change', 'just exploration', 'brainstorming') —"
                " honour that framing in the title and summary."
            )
        parts.extend([
            "",
            "**Narrative shape: discussion.** The underlying turn slice "
            "contains no file edits. Compose Discussion bullets (what "
            "was talked through — model-proposed options, considered "
            "trade-offs, lines of reasoning the user did NOT explicitly "
            "ratify) and Loose ends (open threads, past-tense). The "
            "schema does NOT include `decisions[]` or `worked_on[]` for "
            "this shape — do not attempt to emit them; they would be "
            "rejected." + intent_note,
            "",
            "Title shape: title MUST NOT promise work that did not "
            "happen. If you are reaching for a deliverable verb "
            "('Refactor', 'Add', 'Fix', 'Implement', 'Migrate', 'Build', "
            "'Ship', 'Create', 'Delete', 'Replace'), prefix it with "
            "'Discussed:' / 'Explored:' / 'Sketched:' / 'Reviewed:' OR "
            "rephrase as a noun phrase. The deliverable verb on its own "
            "lies about what the session produced.",
        ])
    elif shape is not None:
        parts.extend([
            "",
            "**Narrative shape: work.** The underlying turn slice "
            "contains real file edits. Compose decisions (substantive "
            "user-confirmed choices, rationale-bearing — leave the "
            "array empty if no clear ratification appeared in the "
            "slice; an empty decisions array is acceptable and often "
            "correct), worked_on (narrative bullets of what was "
            "actually changed), and loose_ends.",
        ])

    if is_continuation and continues_wikilink:
        parts.extend([
            "",
            f"This note CONTINUES {continues_wikilink}. Do not re-summarise "
            "the prior part; focus on THIS part's arc only.",
        ])
    if activity_summary:
        parts.extend([
            "",
            "Activity already populated (do not duplicate verbatim, but "
            "use it to anchor the narrative):",
            activity_summary,
        ])
    if turns_text:
        parts.extend([
            "",
            "Conversation slice:",
            turns_text,
        ])
    else:
        # No transcript content available — make the LLM stay close to
        # the deterministic activity rather than confabulating a story.
        # Without this guardrail, mid-tier models reach for generic
        # engineering narratives that have nothing to do with the work.
        parts.extend([
            "",
            "No conversation slice is available for this flush. Compose "
            "the narrative *strictly* from the activity bullets above — "
            "do NOT invent decisions, designs, or details that aren't "
            "directly evidenced by the file paths, commit subjects, "
            "plan / project references, or issue numbers shown. If the "
            "signal is too thin to write a substantive summary, keep "
            "the summary terse and factual ('Touched X, Y, Z; no "
            "narrative reconstruction available.') rather than padding.",
        ])
    return "\n".join(parts)


def _phase2_tool_schema(shape: NarrativeShape | None = None) -> dict[str, Any]:
    """Build the ``compose`` tool schema for the given narrative shape.

    Two variants:

    - **work** (``shape.has_edits`` or ``shape is None``): existing
      fields — ``decisions[]``, ``worked_on[]``, ``loose_ends[]``.
    - **discussion** (``not shape.has_edits``): replaces ``worked_on``
      with ``discussion`` and OMITS ``decisions`` entirely. The
      schema's ``additionalProperties: false`` is what makes this gate
      structural rather than instructional.

    ``shape=None`` preserves the work-shape behaviour for tests and
    callers that haven't migrated.
    """
    is_discussion = shape is not None and not shape.has_edits

    common: dict[str, Any] = {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "summary": {"type": "string"},
        "loose_ends": {
            "type": "array",
            "maxItems": BULLET_CAPS["loose_ends"],
            "items": {"type": "string", "maxLength": BULLET_LINE_MAX},
        },
    }

    if is_discussion:
        properties: dict[str, Any] = {
            **common,
            "discussion": {
                "type": "array",
                "maxItems": BULLET_CAPS.get("discussion", BULLET_CAPS["worked_on"]),
                "items": {"type": "string", "maxLength": BULLET_LINE_MAX},
            },
        }
    else:
        properties = {
            **common,
            "decisions": {
                "type": "array",
                "maxItems": BULLET_CAPS["decisions"],
                "items": {"type": "string", "maxLength": BULLET_LINE_MAX},
            },
            "worked_on": {
                "type": "array",
                "maxItems": BULLET_CAPS["worked_on"],
                "items": {"type": "string", "maxLength": BULLET_LINE_MAX},
            },
        }

    return {
        "name": "compose",
        "description": "Emit the narrative for the session note.",
        "input_schema": {
            "type": "object",
            "properties": properties,
            # Structural gate, not instructional. Without this, an LLM
            # that decides to emit ``decisions[]`` in discussion shape
            # would silently pass through — the SDK doesn't validate
            # tool_use responses against the schema we sent.
            "additionalProperties": False,
            "required": ["title", "description", "summary"],
        },
    }


def _extract_tool_input(resp: Any) -> dict[str, Any]:
    for block in getattr(resp, "content", []) or []:
        block_type = getattr(block, "type", None)
        if block_type == "tool_use":
            inp = getattr(block, "input", None)
            if isinstance(inp, dict):
                return inp
    return {}


def _truncate_bullets(items: list[str] | None, *, cap: int) -> list[str]:
    """Cap count and per-line length defensively (post-LLM)."""
    if not items:
        return []
    out: list[str] = []
    for raw in items[:cap]:
        if not isinstance(raw, str):
            continue
        line = raw.strip()
        if not line:
            continue
        if len(line) > BULLET_LINE_MAX:
            line = line[: BULLET_LINE_MAX - 1].rstrip() + "…"
        out.append(line)
    return out


def _bulletise(items: list[str]) -> list[str]:
    """Wrap each line with ``- `` (round-trip with parse_body_sections)."""
    return [f"- {x}" for x in items]


def _activity_summary_text(rb: ReplayedBuffer) -> str:
    """Render the deterministic Activity into prompt-friendly bullets.

    Surfaces ``files_modified`` (edits only — what the session actually
    changed) and, when distinct, ``files_read`` (browsed but not edited).
    Falls back to the legacy ``files_touched`` union for archived v1
    buffers whose JSONL events predate the split. The legacy
    ``Files touched:`` framing has been retired everywhere except as a
    last-resort fallback so the LLM doesn't conflate reads with edits
    in the narrative tense.
    """
    lines: list[str] = []
    if rb.activity_commits:
        lines.append("Commits:")
        lines.extend(rb.activity_commits)
    if rb.activity_issues_opened:
        lines.append("Issues opened:")
        lines.extend(rb.activity_issues_opened)
    if rb.activity_issues_closed:
        lines.append("Issues closed:")
        lines.extend(rb.activity_issues_closed)
    modified_set = set(rb.files_modified)
    if rb.files_modified:
        lines.append("Files modified: " + ", ".join(rb.files_modified[:30]))
    read_extra = [f for f in rb.files_read if f not in modified_set]
    if read_extra:
        lines.append("Files read: " + ", ".join(read_extra[:30]))
    if not rb.files_modified and not read_extra and rb.files_touched:
        # Legacy v1 archive fallback — no split data on disk.
        lines.append("Files touched: " + ", ".join(rb.files_touched[:30]))
    if rb.projects:
        lines.append("Projects referenced: " + ", ".join(rb.projects))
    return "\n".join(lines)


def _read_slice_turns(
    *,
    sidecar: Sidecar,
    rb: ReplayedBuffer,
    adapter_lookup,
) -> list[Turn]:
    """Best-effort: reconstruct the ``Turn`` list the buffer pointed at.

    Uses the adapter's ``read_slice(from_index)`` API, capped to the
    buffer's slice pointers. Returns an empty list when the adapter
    can't be loaded or the transcript file no longer exists — Phase 2
    callers degrade gracefully (text-only path uses ``""``; shape-
    selection path treats empty as "no signal").
    """
    if not rb.slices:
        return []
    try:
        adapter: Adapter = adapter_lookup(sidecar.integration)
    except Exception:  # noqa: BLE001
        return []
    try:
        # Sidecar lacks a full TranscriptHandle (only cwd + integration).
        # Re-build a minimal handle; ``read_slice`` only uses ``path``,
        # ``cwd``, ``id``, ``integration``.
        from datetime import UTC as _UTC
        path_attr = getattr(adapter, "transcript_path_for_id", None)
        if callable(path_attr):
            tx_path = path_attr(sidecar.transcript_id, Path(sidecar.cwd))
        else:
            tx_path = None
        if tx_path is None:
            return []
        handle = TranscriptHandle(
            integration=sidecar.integration,
            id=sidecar.transcript_id,
            path=tx_path,
            cwd=Path(sidecar.cwd),
            mtime=datetime.now(_UTC),
        )
    except Exception:  # noqa: BLE001
        return []
    first_idx = min(s.from_index for s in rb.slices)
    last_idx = max(s.to_index for s in rb.slices)
    out: list[Turn] = []
    try:
        for turn in adapter.read_slice(handle, from_index=first_idx):
            if turn.index > last_idx:
                break
            out.append(turn)
    except Exception:  # noqa: BLE001 - never crash a flush on adapter failures
        return out
    return out


def _read_slice_text(
    *,
    sidecar: Sidecar,
    rb: ReplayedBuffer,
    adapter_lookup,
) -> str:
    """Format the slice's turns into the prompt-friendly text Phase 2
    consumes. Wraps :func:`_read_slice_turns` so the two paths agree on
    adapter loading + slice bounds.
    """
    turns = _read_slice_turns(
        sidecar=sidecar, rb=rb, adapter_lookup=adapter_lookup,
    )
    return "\n".join(
        f"[{t.role}@{t.index}] {t.text}" for t in turns if t.text
    )


def _phase2_apply(
    *,
    stub_path: Path,
    composed: dict[str, Any],
    wiki_root: Path,
    rb: ReplayedBuffer,
    sidecar: Sidecar,
    logger: "RunLogger | None" = None,
    shape: NarrativeShape | None = None,
    in_place: bool = False,
) -> Path:
    """Rewrite the finalised stub with the LLM-composed narrative.

    Returns the final stub path. Phase 2 may rename the file from the
    deterministic-slug stub (often a fallback like ``session-<scope>-<HHMM>``
    or a generic basename) to one derived from the synthesised title —
    the title is the most authoritative naming signal we ever get for
    the note. Old stem is preserved as a frontmatter ``aliases:`` entry
    so existing ``[[old-stem]]`` references keep resolving.

    ``shape`` (added in step-8 of the conditional-Decisions plan) drives
    a small frontmatter surfacing: when ``shape.adr_flagged`` is True
    (the user explicitly invoked ADR vocabulary), ``adr_flagged: true``
    is written to frontmatter so a future ``lore curator promote-adr``
    flow can find candidates without re-scanning transcripts. No
    auto-stub creation: the regex cue alone never mutates the vault.
    """
    text = stub_path.read_text()
    fm = parse_frontmatter(text)
    body_text = strip_frontmatter(text)  # noqa: F841 - body re-rendered from scratch

    title = (composed.get("title") or "").strip() or fm.get("title") or "session"
    description = (
        (composed.get("description") or "").strip()
        or fm.get("description")
        or STUB_DESCRIPTION_PLACEHOLDER
    )
    summary = (composed.get("summary") or "").strip() or description

    decisions = _truncate_bullets(composed.get("decisions"), cap=BULLET_CAPS["decisions"])
    worked_on = _truncate_bullets(composed.get("worked_on"), cap=BULLET_CAPS["worked_on"])
    loose_ends = _truncate_bullets(composed.get("loose_ends"), cap=BULLET_CAPS["loose_ends"])
    discussion = _truncate_bullets(
        composed.get("discussion"),
        cap=BULLET_CAPS.get("discussion", BULLET_CAPS["worked_on"]),
    )

    fm["title"] = title
    fm["description"] = description
    if shape is not None and shape.adr_flagged:
        fm["adr_flagged"] = True
    fm["last_reviewed"] = datetime.now(UTC).date().isoformat()
    if in_place:
        # Retain the stub marker so the merge gate keeps the
        # "buffer is live" semantic across the title rewrite.
        fm.setdefault("state", STUB_FRONTMATTER_STATE)

    # Pick the final path. May rename when the synthesised title yields
    # a richer slug than the deterministic stub. In-place mode allows
    # the rename only when no prior rename has happened (signal:
    # ``aliases:`` is empty / absent). Subsequent in-place syntheses
    # update the title in frontmatter but never rename the file again,
    # so existing ``[[<title-slug>]]`` references keep resolving.
    final_path = _resolve_phase2_path(
        stub_path=stub_path, title=title, sidecar=sidecar,
        allow_rename=(not in_place) or not fm.get("aliases"),
    )
    if final_path != stub_path:
        old_stem = stub_path.stem
        existing_aliases = fm.get("aliases") or []
        if isinstance(existing_aliases, str):
            existing_aliases = [existing_aliases]
        if old_stem not in existing_aliases:
            existing_aliases = [*existing_aliases, old_stem]
        fm["aliases"] = existing_aliases

    body = render_body_sections(BodySections(
        title=title,
        summary=summary,
        decisions=_bulletise(decisions),
        worked_on=_bulletise(worked_on),
        loose_ends=_bulletise(loose_ends),
        commits=rb.activity_commits,
        issues_opened=rb.activity_issues_opened,
        issues_closed=rb.activity_issues_closed,
        discussion=_bulletise(discussion),
    ))
    new_text = _render_markdown(fm, body, wiki_root=wiki_root)
    atomic_write_text(stub_path, new_text)

    if final_path != stub_path:
        # Rename in place. ``os.replace`` is atomic on POSIX; on a
        # collision we fall back to incrementing the slug counter.
        import os

        try:
            os.replace(stub_path, final_path)
        except OSError as exc:
            # Rename failed (e.g., cross-device) — keep the old path,
            # drop the alias we added since the rename didn't happen.
            if logger is not None:
                logger.emit(
                    "warning",
                    call="phase2-rename",
                    message=f"rename {stub_path.name} → {final_path.name} failed: {exc}",
                )
            return stub_path
        if logger is not None:
            logger.emit(
                "stub-renamed-on-synthesis",
                transcript_id=sidecar.transcript_id,
                buffer_stem=sidecar.buffer_stem if hasattr(sidecar, "buffer_stem") else "",
                old_path=str(stub_path),
                new_path=str(final_path),
            )
        return final_path
    return stub_path


# Pattern for the canonical session filename: "<DD>-<HHMM>-<slug>.md".
# The slug-portion can itself contain hyphens; we use a maxsplit=2 split
# on the stem to peel off the date and time prefixes safely.
_SESSION_STEM_PARTS = 3


def _resolve_phase2_path(
    *, stub_path: Path, title: str, sidecar: Sidecar,
    allow_rename: bool = True,
) -> Path:
    """Return the path the Phase 2 note should live at.

    Equal to ``stub_path`` when:
    * ``allow_rename`` is False (caller has decided the rename slot is
      already used — e.g., a prior in-place synth has already renamed
      and stamped ``aliases:``);
    * the note is part 2+ of a continuation chain (renaming would
      orphan ``continued_by`` / ``continues`` cross-references);
    * the synthesised title is empty / equals the placeholder;
    * the title-derived slug already matches the stub's slug-portion;
    * the stub path doesn't conform to ``<DD>-<HHMM>-<slug>.md``.

    Otherwise: the same directory and ``<DD>-<HHMM>-`` prefix with the
    slug-portion replaced by ``_slug(title)``, suffixed with a collision
    counter when needed.
    """
    if not allow_rename:
        return stub_path
    if sidecar.part_index >= 2 or sidecar.continuation_of:
        return stub_path
    if not title.strip():
        return stub_path
    new_slug = _slug(title)
    if not new_slug or new_slug == "session":
        return stub_path
    parts = stub_path.stem.split("-", _SESSION_STEM_PARTS - 1)
    if len(parts) < _SESSION_STEM_PARTS:
        return stub_path
    current_slug = parts[_SESSION_STEM_PARTS - 1]
    if new_slug == current_slug:
        return stub_path
    prefix = f"{parts[0]}-{parts[1]}-"
    parent = stub_path.parent
    candidate = parent / f"{prefix}{new_slug}.md"
    counter = 1
    while candidate.exists() and candidate != stub_path:
        counter += 1
        candidate = parent / f"{prefix}{new_slug}-{counter}.md"
    return candidate


# ---------------------------------------------------------------------------
# Top-level entry
# ---------------------------------------------------------------------------


def synth_and_close(
    buffer_path: Path,
    *,
    lore_root: Path,
    wiki_root: Path,
    llm_client: Any = None,
    model: str | None = None,
    adapter_lookup=None,
    logger: "RunLogger | None" = None,
    auto_commit: bool = True,
) -> FlushOutcome:
    """Phase 1 + Phase 2, then transition to ``closed`` and archive.

    Used by cap-trip and the reaper — paths where the buffer should
    legitimately close (cap-trip honours an LLM-context limit; the
    reaper has decided the conversation is over). The note ends with
    ``state: stub`` removed, the sidecar+log moved to ``_done/``, and
    Phase 2's narrative applied if an LLM client was provided.
    """
    return _synthesize(
        buffer_path,
        lore_root=lore_root,
        wiki_root=wiki_root,
        llm_client=llm_client,
        model=model,
        adapter_lookup=adapter_lookup,
        logger=logger,
        auto_commit=auto_commit,
        close=True,
    )


def synth_in_place(
    buffer_path: Path,
    *,
    lore_root: Path,
    wiki_root: Path,
    llm_client: Any = None,
    model: str | None = None,
    adapter_lookup=None,
    logger: "RunLogger | None" = None,
    auto_commit: bool = True,
) -> FlushOutcome:
    """Phase 1 + Phase 2 against the live buffer, leave it accumulating.

    Pre-compact and session-end fire this so the user-visible note is
    up-to-date for handover *without* fragmenting the transcript into
    multiple notes. The buffer stays in ``accumulating``; ``state: stub``
    is retained on the note's frontmatter so the merge gate's
    stub-protection branch keeps treating it as "buffer is live".

    Idempotent across multiple calls within the same buffer's lifetime:
    the first call may rename the file from a deterministic-slug stem to
    a title-derived stem (and stamp ``aliases:``); subsequent calls
    update the title in frontmatter but never rename the file again.
    """
    return _synthesize(
        buffer_path,
        lore_root=lore_root,
        wiki_root=wiki_root,
        llm_client=llm_client,
        model=model,
        adapter_lookup=adapter_lookup,
        logger=logger,
        auto_commit=auto_commit,
        close=False,
    )


def flush_buffer(
    buffer_path: Path,
    *,
    lore_root: Path,
    wiki_root: Path,
    llm_client: Any = None,
    model: str | None = None,
    adapter_lookup=None,
    logger: "RunLogger | None" = None,
    auto_commit: bool = True,
) -> FlushOutcome:
    """Backwards-compatible alias for :func:`synth_and_close`.

    ``buffer_path`` is the sidecar path (``<stem>.state.json``). New
    callers should pick :func:`synth_and_close` (cap-trip / reaper) or
    :func:`synth_in_place` (session-end / pre-compact) explicitly so
    the buffer state-machine intent stays visible at the call site.
    """
    return synth_and_close(
        buffer_path,
        lore_root=lore_root,
        wiki_root=wiki_root,
        llm_client=llm_client,
        model=model,
        adapter_lookup=adapter_lookup,
        logger=logger,
        auto_commit=auto_commit,
    )


def _synthesize(
    buffer_path: Path,
    *,
    lore_root: Path,
    wiki_root: Path,
    llm_client: Any = None,
    model: str | None = None,
    adapter_lookup=None,
    logger: "RunLogger | None" = None,
    auto_commit: bool = True,
    close: bool = True,
) -> FlushOutcome:
    """Drive Phase 1 + (optionally) Phase 2 to completion.

    ``close=True`` (``synth_and_close``): CAS through
    ``ready -> flushing -> closed`` and archive to ``_done/``.
    ``close=False`` (``synth_in_place``): keep the buffer in
    ``accumulating`` and never archive — Phase 1 just refreshes the
    on-disk stub and Phase 2's LLM narrative replaces the placeholder
    title / summary in place.

    Returns a :class:`FlushOutcome` even on partial success.
    """
    buffer = Buffer.from_sidecar_path(buffer_path)
    sidecar = buffer.read_sidecar()
    if sidecar is None:
        return FlushOutcome(buffer_stem=buffer.stem, skipped_reason="no-sidecar")

    if sidecar.state == "closed":
        return FlushOutcome(
            buffer_stem=buffer.stem,
            state_before="closed",
            skipped_reason="already-closed",
        )

    # Phase 1 (deterministic) under flock.
    with buffer.with_lock():
        sidecar = buffer.read_sidecar()
        if sidecar is None or sidecar.state == "closed":
            return FlushOutcome(
                buffer_stem=buffer.stem,
                state_before="closed",
                skipped_reason="closed-during-acquire",
            )
        if close:
            # accumulating -> ready -> flushing (CAS) — accept either entry.
            if sidecar.state == "accumulating":
                sidecar = buffer.transition("ready")
            if sidecar.state == "ready":
                sidecar = buffer.transition("flushing")
        # In-place mode: do NOT transition state. Phase 1 / Phase 2 run
        # against the live ``accumulating`` buffer; subsequent heartbeats
        # continue to fold into it.
        rb = buffer.replay()
        outcome = _phase1_finalise(
            buffer=buffer, sidecar=sidecar, rb=rb,
            wiki_root=wiki_root, logger=logger,
            in_place=not close,
        )
        # Drain emit (note-filed) before closing — only on close path.
        # In-place writes don't promote handover state; the note is still
        # owned by the live buffer.
        if close and outcome.phase1_completed and outcome.stub_path is not None:
            _drain_emit_filed(
                lore_root=lore_root, sidecar=sidecar, outcome=outcome, logger=logger,
            )
        if close:
            # Close the buffer regardless of Phase 1 stub presence — handover
            # gates on state=closed, not on stub presence.
            buffer.transition("closed")
        else:
            # In-place: clear the request marker so the next heartbeat /
            # CLI run doesn't loop on the same accumulating buffer. Done
            # under the existing flock; the buffer is still alive.
            sc_after = buffer.read_sidecar()
            if sc_after is not None and sc_after.flush_requested is not None:
                buffer.patch(flush_requested=None)

    if close:
        # Move to _done/ outside the flock (the file moves; lockfile cleanup
        # is best-effort and idempotent under concurrent reapers).
        try:
            buffer.close()
        except OSError:
            pass
    if outcome.phase1_completed and outcome.stub_path and auto_commit:
        try:
            filed = FiledNote(
                path=outcome.stub_path,
                wikilink=outcome.wikilink,
                was_merge=False,
            )
            maybe_auto_commit(wiki_root, filed, logger, llm_client=None)
        except Exception:  # noqa: BLE001 - never block on git
            pass

    # Phase 2 (LLM) — outside the flock; the buffer is already closed
    # and rewriting an in-place stub is safe (single writer for this
    # buffer's stub, since other processes see state=closed).
    if not outcome.phase1_completed or outcome.stub_path is None:
        return outcome
    if llm_client is None or not model:
        return outcome

    adapter_lookup = adapter_lookup or get_adapter
    sidecar = _find_buffer_sidecar(lore_root, buffer.stem) or sidecar
    rb_post = buffer.replay()
    if not rb_post.slices and rb.slices:
        rb_post = rb  # buffer was moved to _done; replay() now returns empty
    turns_list = _read_slice_turns(
        sidecar=sidecar, rb=rb_post or rb, adapter_lookup=adapter_lookup,
    )
    turns_text = "\n".join(
        f"[{t.role}@{t.index}] {t.text}" for t in turns_list if t.text
    )
    # Narrative shape drives Phase-2 schema + prompt gating in step-4.
    # Recompute ``files_modified`` from the actual turns rather than
    # trusting the buffer's accumulator: archived v1 buffers don't carry
    # ``files_modified`` in their event log (it stays empty), but the
    # transcript adapter still has the truth.
    files_modified = _files_modified_from_turns(turns_list)
    shape = select_shape(turns_list, files_modified)
    if logger is not None:
        logger.emit(
            "narrative-shape",
            transcript_id=sidecar.transcript_id,
            buffer_stem=buffer.stem,
            kind=shape.kind,
            has_edits=shape.has_edits,
            decisions_allowed=shape.decisions_allowed,
            no_edit_intent=shape.no_edit_intent,
            adr_flagged=shape.adr_flagged,
            files_modified_count=len(files_modified),
        )
    activity_summary = _activity_summary_text(rb_post or rb)
    continues_wikilink = (
        f"[[{sidecar.continuation_of}]]"
        if sidecar.part_index >= 2 and sidecar.continuation_of
        else None
    )

    # Guard: don't ask the LLM to fabricate a narrative from boilerplate
    # alone. When the conversation slice is empty AND the activity has
    # no commits / projects to anchor on, the model confabulates
    # confidently — silently producing a fictional session note. Better
    # to keep the deterministic Phase 1 stub and mark the flush degraded.
    rb_for_signal = rb_post or rb
    has_signal = bool(turns_text) or bool(
        rb_for_signal.activity_commits
        or rb_for_signal.projects
    )
    if not has_signal:
        outcome.degraded = True
        if logger is not None:
            logger.emit(
                "flush-degraded",
                transcript_id=sidecar.transcript_id,
                buffer_stem=buffer.stem,
                reason="empty-signal-skipped-llm",
                turns_text_chars=len(turns_text),
                files_modified=len(rb_for_signal.files_modified),
                files_read=len(rb_for_signal.files_read),
                commits=len(rb_for_signal.activity_commits),
            )
        return outcome

    composed: dict[str, Any] | None = None
    attempts = 0
    while attempts < PHASE2_MAX_ATTEMPTS:
        attempts += 1
        composed = compose_session_note(
            turns_text=turns_text,
            activity_summary=activity_summary,
            is_continuation=sidecar.part_index >= 2,
            continues_wikilink=continues_wikilink,
            llm_client=llm_client,
            model=model,
            logger=logger,
            transcript_id=sidecar.transcript_id,
            shape=shape,
        )
        if composed:
            break
    outcome.phase2_attempts = attempts

    if not composed:
        outcome.degraded = True
        if logger is not None:
            logger.emit(
                "flush-degraded",
                transcript_id=sidecar.transcript_id,
                buffer_stem=buffer.stem,
                attempts=attempts,
            )
        return outcome

    try:
        final_path = _phase2_apply(
            stub_path=outcome.stub_path,
            composed=composed,
            wiki_root=wiki_root,
            rb=rb_post or rb,
            sidecar=sidecar,
            logger=logger,
            shape=shape,
            in_place=not close,
        )
    except OSError as exc:
        outcome.degraded = True
        if logger is not None:
            logger.emit(
                "warning",
                call="phase2-apply",
                message=f"failed to rewrite stub: {exc}",
            )
        return outcome

    if final_path != outcome.stub_path:
        outcome.stub_path = final_path
        outcome.wikilink = f"[[{final_path.stem}]]"
    outcome.phase2_completed = True
    outcome.composed = composed
    if logger is not None:
        logger.emit(
            "flush-llm-completed",
            transcript_id=sidecar.transcript_id,
            buffer_stem=buffer.stem,
            attempts=attempts,
            in_place=not close,
        )
    # ``flush_requested`` was cleared inside the flock above on the
    # in-place path; close path archives to ``_done/`` so the sidecar
    # is gone either way.
    return outcome


def spawn_detached_flush(buffer_path: Path, *, lore_root: Path) -> bool:
    """Fire-and-forget ``lore curator flush <buffer-path> --config-from-buffer``.

    Returns True on successful Popen, False on OSError. Never blocks
    the caller. Per-buffer spawn-lock under
    ``.lore/buffers/<stem>.spawn.lock`` prevents double-spawn if a
    reaper races with cap-trip; if the lock is held, we skip rather
    than queueing.
    """
    import os
    import subprocess
    import sys

    from lore_core.lockfile import flocked

    spawn_lock = buffer_path.with_suffix(".spawn.lock")
    try:
        with flocked(spawn_lock, blocking=False) as held:
            if not held:
                return False
            cmd = [
                sys.executable, "-m", "lore_cli", "curator", "flush",
                "--config-from-buffer", str(buffer_path),
            ]
            env = os.environ.copy()
            env["LORE_ROOT"] = str(lore_root)
            env["LORE_CURATOR_MODE"] = "1"
            try:
                subprocess.Popen(
                    cmd, cwd=str(lore_root),
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    env=env,
                )
                return True
            except (OSError, subprocess.SubprocessError):
                return False
    except OSError:
        return False


def _drain_emit_filed(
    *,
    lore_root: Path,
    sidecar: Sidecar,
    outcome: FlushOutcome,
    logger: "RunLogger | None",
) -> None:
    try:
        from lore_core.drain import DrainStore, resolve_session_id

        sid, _ = resolve_session_id(Path(sidecar.cwd))
        DrainStore(lore_root, sid).emit(
            "note-filed",
            wiki=sidecar.wiki,
            wikilink=outcome.wikilink,
            path=str(outcome.stub_path) if outcome.stub_path else "",
            transcript_id=sidecar.transcript_id,
        )
    except Exception:  # noqa: BLE001
        pass
