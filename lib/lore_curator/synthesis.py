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
   ``summary`` / ``narrative`` stay empty until Phase 2 fills them.
5. Atomically rewrite the stub at ``sidecar.stub_path``; drop
   frontmatter ``state: stub``; for Part >= 2 add ``part: N`` and
   ``continues: [[<prev>]]``.
6. CAS ``flushing -> closed`` — handover unblocks here.
7. Move sidecar+log to ``.lore/buffers/_done/``.

The Phase-1 note is already useful by itself — Activity (commits /
issues / files) is the most evidentiary part of any session note.

Phase 2 — LLM composition, two-call P2 (experiment 005 best-GPT-OSS
cell at 0.804 / 0.964 hero):

1. **Call A — `outline`**: cheap first pass. Model emits 4-8 short
   outline items (≤8 words each, no grounding yet) from the transcript
   slice. ``{items}`` is the entire schema.
2. **Call B — `compose`**: expand pass. Model receives the outline +
   transcript and emits ``{title, summary_lede, narrative}``. The
   narrative is a single markdown string with bold-led bullets, ``@N``
   turn citations, optional sub-headings (``### Strategy / ### Detours
   / ### Outcomes``), and per-bullet epistemic prefixes
   (``Considered: / Leaning: / Tried: / Open:``) for tentative items.
3. For Part >= 2, both prompts get a rider: "this continues
   [[<prev>]]; focus on THIS part's arc only".
4. Re-open the finalised stub, rewrite frontmatter ``title`` /
   ``description`` and body sections (``# H1``, ``## Summary``,
   ``## Narrative``, ``## Activity``). Activity / files_touched /
   plans / projects / provenance preserved. The structured side-fields
   the pre-P2 schema produced (``adr_candidates`` / ``worked_on`` /
   ``discussion`` / ``loose_ends``) are not emitted; legacy notes keep
   them on round-trip via the parser.
5. On failure on either call: ``flush_attempts++``, retry up to 3
   times in-process. On exhaustion, emit ``flush-degraded`` and leave
   the Activity-only note intact. **No state rollback** — buffer stays
   ``closed``.
6. For Part >= 2, back-fill ``continued_by: [[<this>]]`` onto the prior
   part's frontmatter.

Tier note: P2 is a GPT-OSS-class-or-better recipe. Mistral-119B
collapsed on this shape in experiment 005 (0.426 vs 0.804) because
the narrative-string schema can't structurally block ``from_example``
copy-paste; a weaker model with poor grounding would degrade quality
without the harness noticing. The README recommends GPT-OSS-120B +
``reasoning_effort=high`` for this path.
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
from lore_core.regions import render_regions
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
    BufferTransitionError,
    ReplayedBuffer,
    Sidecar,
    _now_iso,
    done_dir,
)
from lore_curator.session_filer import _slug
from lore_curator.stub_note import (
    STUB_DESCRIPTION_PLACEHOLDER,
    STUB_FRONTMATTER_STATE,
    STUB_NARRATIVE_SENTINEL,
    STUB_SUMMARY_PLACEHOLDER,
    file_lists_for_frontmatter,
)

if TYPE_CHECKING:
    from lore_core.run_log import RunLogger


__all__ = [
    "FlushOutcome",
    "compose_session_note",
    "synth_in_place",
    "synth_and_close",
    "spawn_detached_flush",
    "OUTLINE_MIN_ITEMS",
    "OUTLINE_MAX_ITEMS",
    "OUTLINE_ITEM_WORD_CAP",
    "SUMMARY_LEDE_MAX",
]


# P2 is shape-agnostic — kind classification moves into per-bullet
# epistemic prefixes inside the narrative string (`Considered:` /
# `Leaning:` / `Tried:` / `Open:`), so the per-section caps that gated
# the old work/discussion split are gone. The only schema cap that still
# matters is the outline length; everything else is prompt-shaped.
OUTLINE_MIN_ITEMS = 4
OUTLINE_MAX_ITEMS = 8
OUTLINE_ITEM_WORD_CAP = 8

# Lede length cap, matching experiment 005 P2 (≤220 chars). Up from the
# pre-P2 ledeshape (160) — the longer cap gives the model room to name
# both topic and outcome in one sentence without scattering into bullets.
SUMMARY_LEDE_MAX = 220
TITLE_WORD_MIN = 6
TITLE_WORD_MAX = 10

PHASE2_MAX_ATTEMPTS = 3
PHASE2_MAX_OUTPUT_TOKENS = 4000


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
        # Closing the buffer ends the live-stub lifecycle: drop the
        # preview sentinel and reset the description to the deterministic
        # placeholder so the closed-but-unsynthesised note doesn't carry
        # a "Live stub …" framing that's no longer accurate. Phase 2
        # overwrites both fields if it runs.
        if fm.get("narrative") == STUB_NARRATIVE_SENTINEL:
            fm.pop("narrative", None)
            fm["description"] = STUB_DESCRIPTION_PLACEHOLDER
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
        adr_candidates=[],
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
) -> dict[str, Any] | None:
    """Two-call P2 composition: outline → expand → ``{title, summary_lede, narrative}``.

    Returns ``None`` on any failure of either call (LLM exception,
    malformed tool_use, empty output). The caller increments
    ``flush_attempts`` and retries up to :data:`PHASE2_MAX_ATTEMPTS`;
    each retry redoes both calls.

    Shape-agnostic: the P2 schema is the same for work and discussion
    sessions. The per-bullet epistemic prefix (``Considered: /
    Leaning: / Tried: / Open:``) carries the kind information that the
    pre-P2 work/discussion gating used to enforce structurally.
    """
    outline_items = _p2_call_outline(
        turns_text=turns_text,
        activity_summary=activity_summary,
        is_continuation=is_continuation,
        continues_wikilink=continues_wikilink,
        llm_client=llm_client,
        model=model,
        logger=logger,
        transcript_id=transcript_id,
    )
    if not outline_items:
        return None
    data = _p2_call_compose(
        outline_items=outline_items,
        turns_text=turns_text,
        activity_summary=activity_summary,
        is_continuation=is_continuation,
        continues_wikilink=continues_wikilink,
        llm_client=llm_client,
        model=model,
        logger=logger,
        transcript_id=transcript_id,
    )
    if data is None:
        return None
    data["outline_items"] = outline_items
    return data


def _p2_call_outline(
    *,
    turns_text: str,
    activity_summary: str,
    is_continuation: bool,
    continues_wikilink: str | None,
    llm_client: Any,
    model: str,
    logger: "RunLogger | None",
    transcript_id: str,
) -> list[str] | None:
    prompt = _p2_outline_prompt(
        turns_text=turns_text,
        activity_summary=activity_summary,
        is_continuation=is_continuation,
        continues_wikilink=continues_wikilink,
    )
    schema = _p2_outline_tool_schema()
    if logger is not None:
        logger.emit(
            "llm-prompt",
            call="p2-outline",
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
            tool_choice={"type": "tool", "name": "outline"},
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:  # noqa: BLE001
        if logger is not None:
            logger.emit(
                "warning",
                call="p2-outline",
                message=f"LLM call raised: {type(exc).__name__}: {exc}",
            )
        return None
    latency_ms = int((time.monotonic() - t0) * 1000)
    data = _extract_tool_input(resp)
    if not isinstance(data, dict):
        if logger is not None:
            logger.emit(
                "warning",
                call="p2-outline",
                message="no tool_use block in response",
            )
        return None
    raw_items = data.get("items") or []
    items: list[str] = []
    for raw in raw_items:
        if not isinstance(raw, str):
            continue
        stripped = raw.strip()
        if not stripped:
            continue
        items.append(stripped)
    if not items:
        return None
    if logger is not None:
        logger.emit(
            "llm-response",
            call="p2-outline",
            transcript_id=transcript_id,
            latency_ms=latency_ms,
            outline_items_count=len(items),
            model_resolved=getattr(resp, "model", "") or "",
            reasoning_effort=getattr(resp, "reasoning_effort", None),
        )
    return items


def _p2_call_compose(
    *,
    outline_items: list[str],
    turns_text: str,
    activity_summary: str,
    is_continuation: bool,
    continues_wikilink: str | None,
    llm_client: Any,
    model: str,
    logger: "RunLogger | None",
    transcript_id: str,
) -> dict[str, Any] | None:
    prompt = _p2_compose_prompt(
        outline_items=outline_items,
        turns_text=turns_text,
        activity_summary=activity_summary,
        is_continuation=is_continuation,
        continues_wikilink=continues_wikilink,
    )
    schema = _p2_compose_tool_schema()
    if logger is not None:
        logger.emit(
            "llm-prompt",
            call="p2-compose",
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
    except Exception as exc:  # noqa: BLE001
        if logger is not None:
            logger.emit(
                "warning",
                call="p2-compose",
                message=f"LLM call raised: {type(exc).__name__}: {exc}",
            )
        return None
    latency_ms = int((time.monotonic() - t0) * 1000)
    data = _extract_tool_input(resp)
    if not isinstance(data, dict):
        if logger is not None:
            logger.emit(
                "warning",
                call="p2-compose",
                message="no tool_use block in response",
            )
        return None
    # additionalProperties is set on the schema but the SDK doesn't
    # enforce it — strip unknown keys and log them so model drift
    # surfaces in telemetry.
    allowed_keys = set(schema["input_schema"]["properties"].keys())
    extra_keys = sorted(k for k in data.keys() if k not in allowed_keys)
    if extra_keys and logger is not None:
        logger.emit(
            "warning",
            call="p2-compose-extra-key",
            transcript_id=transcript_id,
            extra_keys=extra_keys,
        )
    data = {k: v for k, v in data.items() if k in allowed_keys}
    title = _truncate_title_words((data.get("title") or "").strip())
    if title:
        data["title"] = title
    if logger is not None:
        logger.emit(
            "llm-response",
            call="p2-compose",
            transcript_id=transcript_id,
            latency_ms=latency_ms,
            keys=sorted(data.keys()),
            narrative_chars=len(data.get("narrative") or ""),
            model_resolved=getattr(resp, "model", "") or "",
            reasoning_effort=getattr(resp, "reasoning_effort", None),
        )
    return data


def _truncate_title_words(title: str, cap: int = TITLE_WORD_MAX) -> str:
    """Drop trailing words past ``cap``. Last-resort safety net only —
    P2's compose-schema title description already asks for 6-10 words.
    """
    words = title.split()
    if len(words) <= cap:
        return title
    return " ".join(words[:cap])


def _p2_outline_prompt(
    *,
    turns_text: str,
    activity_summary: str,
    is_continuation: bool,
    continues_wikilink: str | None,
) -> str:
    """P2 Call A — outline. Ported verbatim from experiment 005's
    ``prompts/p2_outline_v1.json`` with three lore-only riders appended
    (continuation, activity anchor, empty-slice guardrail).
    """
    parts: list[str] = [
        "Below is a TRANSCRIPT of a past session between a user and an "
        "AI coding assistant, delimited by <<<TRANSCRIPT BEGIN>>> / "
        "<<<TRANSCRIPT END>>>.",
        "",
        f"Your ONLY task is to call the `outline` tool with "
        f"{OUTLINE_MIN_ITEMS}-{OUTLINE_MAX_ITEMS} short outline items "
        "describing what happened in this session.",
        "",
        "Rules:",
        f"- Each item ≤ {OUTLINE_ITEM_WORD_CAP} words.",
        "- One topic per item. Don't compound.",
        "- Plain text, no markdown, no leading bullet.",
        "- Pick from: user intents, decisions, things tried, things "
        "done, loose ends.",
        f"- Pick the MOST IMPORTANT {OUTLINE_MIN_ITEMS}-"
        f"{OUTLINE_MAX_ITEMS} items if there are more candidates.",
        "- Do not include illustrative material from the transcript "
        "(✓/✗ examples, references to other sessions) — focus on what "
        "THIS session worked on.",
        "",
        "This is a FIRST PASS. A second LLM call will expand each item "
        "with evidence from the transcript. Your job is to surface the "
        "right topics, not to ground them yet.",
        "",
        "Call the `outline` tool exactly once.",
    ]
    if is_continuation and continues_wikilink:
        parts.extend([
            "",
            f"This note CONTINUES {continues_wikilink}. Surface topics "
            "from THIS part's arc only — do not re-list topics already "
            "covered in the prior part.",
        ])
    if activity_summary:
        parts.extend([
            "",
            "Activity already populated (deterministic — anchor your "
            "outline to it, don't duplicate verbatim):",
            activity_summary,
        ])
    if turns_text:
        parts.extend([
            "",
            "<<<TRANSCRIPT BEGIN>>>",
            turns_text,
            "<<<TRANSCRIPT END>>>",
        ])
    else:
        # Empty-slice guardrail — keep the outline grounded in the
        # deterministic activity rather than inventing topics.
        parts.extend([
            "",
            "No conversation slice is available. Outline strictly from "
            "the activity bullets above — do not invent topics that "
            "aren't evidenced by file paths, commit subjects, plan / "
            "project references, or issue numbers shown.",
        ])
    return "\n".join(parts)


def _p2_compose_prompt(
    *,
    outline_items: list[str],
    turns_text: str,
    activity_summary: str,
    is_continuation: bool,
    continues_wikilink: str | None,
) -> str:
    """P2 Call B — compose. Ported verbatim from experiment 005's
    ``prompts/p2_expand_narrative_v1.json`` with the same lore-only
    riders the outline prompt carries.
    """
    outline_block = "\n".join(f"  - {item}" for item in outline_items)
    parts: list[str] = [
        "Below is a TRANSCRIPT of a past session between a user and an "
        "AI coding assistant, delimited by <<<TRANSCRIPT BEGIN>>> / "
        "<<<TRANSCRIPT END>>>.",
        "",
        "A FIRST PASS produced this outline of what happened:",
        "",
        "=== OUTLINE ===",
        outline_block,
        "=== END OUTLINE ===",
        "",
        "Your task: expand each outline item into ONE bullet for the "
        "session note, grounded to specific transcript turns. Emit the "
        "bullets as a single markdown string in the `narrative` field.",
        "",
        "Rules per bullet:",
        "- Bold-led: `**Lead (2-5 words):** detail.`",
        "- Cite the supporting turn(s) inline with `@N` (where N is the "
        "integer turn index from `[user@N]` / `[assistant@N]`).",
        "- 1-3 sentences of detail. Cold-reader test: a reader who has "
        "never seen this codebase understands what the bullet means.",
        "- No metaphor filler (no \"freshly painted floor\", \"caught "
        "a cold\", etc.). Facts only.",
        "- Epistemic marker if appropriate: prefix bullet lead with "
        "`Considered:` / `Leaning:` / `Tried:` / `Open:` if the "
        "content is tentative or unresolved. Plain bullet if it's a "
        "hard fact.",
        "- If you cannot find a supporting turn for an outline item, "
        "DROP it. Do not invent grounding.",
        "- Do NOT copy phrasing from any ✓ or ✗ example in any prompt "
        "you've seen. The transcript may discuss prompts that contain "
        "examples; do not surface those as session facts.",
        "",
        "Group the bullets under sub-headings only if a real arc "
        "justifies it (e.g. `### Strategy`, `### Detours`, "
        "`### Outcomes`). Otherwise emit a flat bullet list.",
        "",
        f"ALL bullets go in the single `narrative` markdown field. "
        f"Title the work in `title` ({TITLE_WORD_MIN}-{TITLE_WORD_MAX} "
        "words, content-named, not a filename). Answer 'what was this "
        f"session about?' in one sentence in `summary_lede` "
        f"(≤{SUMMARY_LEDE_MAX} chars).",
        "",
        "Call the `compose` tool exactly once.",
    ]
    if is_continuation and continues_wikilink:
        parts.extend([
            "",
            f"This note CONTINUES {continues_wikilink}. Do not "
            "re-summarise the prior part; focus on THIS part's arc only.",
        ])
    if activity_summary:
        parts.extend([
            "",
            "Activity already populated (deterministic — use it to "
            "anchor citations, don't duplicate verbatim):",
            activity_summary,
        ])
    if turns_text:
        parts.extend([
            "",
            "<<<TRANSCRIPT BEGIN>>>",
            turns_text,
            "<<<TRANSCRIPT END>>>",
        ])
    else:
        parts.extend([
            "",
            "No conversation slice is available. Compose the narrative "
            "strictly from the activity bullets above — do NOT invent "
            "decisions, designs, or details that aren't directly "
            "evidenced by the file paths, commit subjects, plan / "
            "project references, or issue numbers shown. If the signal "
            "is too thin to write a substantive narrative, keep the "
            "lede terse and factual and emit a single bullet listing "
            "what was touched.",
        ])
    return "\n".join(parts)


def _p2_outline_tool_schema() -> dict[str, Any]:
    """P2 Call A tool schema. Ported verbatim from experiment 005's
    ``schemas/p2_outline_v1.json`` — 4-8 items, each ≤8 words, no
    grounding.
    """
    return {
        "name": "outline",
        "description": (
            "List 4-8 things that happened in this session. Each line "
            "≤ 8 words, plain prose, no bullet marker. Just the bare "
            "topics — Call B will expand each one with evidence."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "minItems": OUTLINE_MIN_ITEMS,
                    "maxItems": OUTLINE_MAX_ITEMS,
                    "description": (
                        f"{OUTLINE_MIN_ITEMS}-{OUTLINE_MAX_ITEMS} short "
                        f"outline items. Each ≤{OUTLINE_ITEM_WORD_CAP} words."
                    ),
                    "items": {
                        "type": "string",
                        "description": (
                            "One outline item — a topic or event that "
                            "happened in this session. Plain text, "
                            f"≤{OUTLINE_ITEM_WORD_CAP} words, no leading "
                            "bullet, no markdown. Each item names one "
                            "thing — don't compound items."
                        ),
                    },
                }
            },
            "additionalProperties": False,
            "required": ["items"],
        },
    }


def _p2_compose_tool_schema() -> dict[str, Any]:
    """P2 Call B tool schema. Ported verbatim from experiment 005's
    ``prompts/p2_expand_narrative_v1.json`` — tight ``{title,
    summary_lede, narrative}`` shape. No side-arrays: scattering bullets
    into typed arrays was a failure mode on v2 schemas, so the narrative
    is one markdown string and that is the only place bullets live.
    """
    return {
        "name": "compose",
        "description": (
            "Compose a session-note narrative from the outline, "
            "grounded to transcript turns. The narrative field is a "
            "single markdown string — that is where ALL the bullets go."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": (
                        f"{TITLE_WORD_MIN}-{TITLE_WORD_MAX} words, "
                        "topic/outcome named. Title the WORK, not a "
                        "filename or script identifier."
                    ),
                },
                "summary_lede": {
                    "type": "string",
                    "maxLength": SUMMARY_LEDE_MAX,
                    "description": (
                        f"ONE sentence (≤{SUMMARY_LEDE_MAX} chars) "
                        "answering 'what was this session about?'. "
                        "Plain prose, no bullets."
                    ),
                },
                "narrative": {
                    "type": "string",
                    "description": (
                        "The full session-note narrative as a single "
                        "markdown string. Bold-led bullets are the "
                        "primary content. Optionally grouped under "
                        "sub-headings (### Strategy / ### Detours / "
                        "### Outcomes). ALL bullets go in this field — "
                        "there is no separate `bullets` array. The "
                        "narrative is markdown text, not a structured "
                        "list."
                    ),
                },
            },
            "additionalProperties": False,
            "required": ["title", "summary_lede", "narrative"],
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
    in_place: bool = False,
) -> Path:
    """Rewrite the finalised stub with the P2-composed narrative.

    Returns the final stub path. Phase 2 may rename the file from the
    deterministic-slug stub (often a fallback like ``session-<scope>-<HHMM>``
    or a generic basename) to one derived from the synthesised title —
    the title is the most authoritative naming signal we ever get for
    the note. Old stem is preserved as a frontmatter ``aliases:`` entry
    so existing ``[[old-stem]]`` references keep resolving.

    P2 body shape: ``# Title`` / ``## Summary`` (one-sentence lede) /
    ``## Narrative`` (model's bold-led bullets with @N citations,
    optional sub-headings) / ``## Activity`` (deterministic). The
    pre-P2 sections (``ADR candidates`` / ``What we worked on`` /
    ``Discussion`` / ``Loose ends``) are no longer emitted by new
    flushes; the parser still recognises them for legacy round-trip.
    """
    text = stub_path.read_text()
    fm = parse_frontmatter(text)
    body_text = strip_frontmatter(text)  # noqa: F841 - body re-rendered from scratch

    title = (composed.get("title") or "").strip() or fm.get("title") or "session"
    summary_lede = (composed.get("summary_lede") or "").strip()
    narrative_text = (composed.get("narrative") or "").strip()
    description = (
        summary_lede
        or fm.get("description")
        or STUB_DESCRIPTION_PLACEHOLDER
    )

    fm["title"] = title
    fm["description"] = description
    # Drop the live-stub preview sentinel: the LLM-composed summary,
    # description, and title are now authoritative. Subsequent
    # heartbeats during in-place synthesis must preserve them.
    fm.pop("narrative", None)
    # Persist the outline as a frontmatter breadcrumb — cheap retrieval
    # signal for ``lore_search`` snippets and a sanity check during
    # post-mortem on degraded flushes. Optional; absent when Call A
    # didn't run (legacy buffers flushed before the P2 rollout).
    outline_items = composed.get("outline_items") or []
    outline_items = [s for s in outline_items if isinstance(s, str) and s.strip()]
    if outline_items:
        fm["outline"] = outline_items
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
        summary=summary_lede or description,
        adr_candidates=[],
        worked_on=[],
        loose_ends=[],
        commits=rb.activity_commits,
        issues_opened=rb.activity_issues_opened,
        issues_closed=rb.activity_issues_closed,
        discussion=[],
        narrative=narrative_text,
    ))
    # Two-region wiring (PRD #92, issue #94): the human-only region is
    # unused under P2 — the narrative *is* the canonical content and
    # belongs in the reload-safe region. ``render_regions(body, None)``
    # is a no-op marker-omitting passthrough, kept for shape parity
    # with legacy notes that still carry a human-only block.
    body = render_regions(body, None)
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
        except BufferTransitionError as exc:
            # _done/<stem>.state.json already exists — part-resolution
            # misfired upstream. Surface loudly via the logger; do NOT
            # crash the curator. The pre-existing archive is preserved
            # by Buffer.close itself; we just record the divergence.
            if logger is not None:
                logger.emit(
                    "warning",
                    reason="done-archive-collision",
                    stem=buffer.stem,
                    transcript_id=sidecar.transcript_id,
                    local_date=sidecar.local_date,
                    detail=str(exc),
                )
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
    # P2 is shape-agnostic: the per-bullet epistemic prefix
    # (``Considered: / Leaning: / Tried: / Open:``) carries the kind
    # signal that the pre-P2 work/discussion gate used to enforce
    # structurally. No ``select_shape`` call here.
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
