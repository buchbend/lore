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
from lore_core.schema import parse_frontmatter, strip_frontmatter
from lore_core.session_writer import (
    BodySections,
    FiledNote,
    render_body_sections,
)
from lore_core.types import TranscriptHandle
from lore_core.wikilinks import sanitize_for_write
from lore_curator._auto_commit import maybe_auto_commit
from lore_curator.buffer_store import (
    Buffer,
    ReplayedBuffer,
    Sidecar,
    _now_iso,
    done_dir,
)
from lore_curator.stub_note import (
    STUB_DESCRIPTION_PLACEHOLDER,
    STUB_FRONTMATTER_STATE,
    STUB_SUMMARY_PLACEHOLDER,
)

if TYPE_CHECKING:
    from lore_core.run_log import RunLogger


__all__ = [
    "FlushOutcome",
    "compose_session_note",
    "flush_buffer",
    "spawn_detached_flush",
    "BULLET_CAPS",
    "BULLET_LINE_MAX",
]


# Phase-2 caps. Phrased as a tool-schema constraint AND post-validated.
BULLET_CAPS = {
    "decisions": 5,
    "worked_on": 8,
    "loose_ends": 5,
}
BULLET_LINE_MAX = 120
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
    dangling_plans: list[str] = field(default_factory=list)
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
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Return ``(valid_plans, valid_projects, dangling_plans, dangling_projects)``.

    Emits ``dangling-ref`` telemetry per dropped ref.
    """
    plans, dangling_plans = _validate_refs(rb.plans, subdir=wiki_root / "plans")
    projects, dangling_projects = _validate_refs(rb.projects, subdir=wiki_root / "projects")
    if logger is not None:
        for d in dangling_plans:
            logger.emit("dangling-ref", kind="plan", ref=d, transcript_id=transcript_id)
        for d in dangling_projects:
            logger.emit("dangling-ref", kind="project", ref=d, transcript_id=transcript_id)
    return plans, projects, dangling_plans, dangling_projects


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
) -> FlushOutcome:
    out = FlushOutcome(
        buffer_stem=buffer.stem,
        state_before=sidecar.state,
    )

    plans, projects, dpl, dpr = _strip_dangles(
        rb, wiki_root=wiki_root,
        logger=logger, transcript_id=sidecar.transcript_id,
    )
    out.dangling_plans = dpl
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
            )
        return out

    text = stub_path.read_text()
    fm = parse_frontmatter(text)
    body_text = strip_frontmatter(text)  # noqa: F841 - body is re-rendered

    # Clear stub marker; preserve title / description placeholders for
    # Phase 2 to rewrite. The Activity-only note is now "final" enough
    # that the handover treats it as filed.
    fm.pop("state", None)
    fm["last_reviewed"] = datetime.now(UTC).date().isoformat()
    fm["curator_a_run"] = datetime.now(UTC).isoformat()
    fm.setdefault("title", fm.get("title", "session"))
    fm.setdefault("description", fm.get("description", STUB_DESCRIPTION_PLACEHOLDER))
    fm.setdefault("type", "session")
    fm.setdefault("schema_version", 2)
    fm.setdefault("scope", sidecar.scope)
    if rb.files_touched:
        fm["files_touched"] = rb.files_touched
    if plans:
        fm["plans"] = plans
    elif "plans" in fm and dpl:
        fm.pop("plans", None)
    if projects:
        fm["projects"] = projects
    elif "projects" in fm and dpr:
        fm.pop("projects", None)
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
            files_touched_count=len(rb.files_touched),
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
    """One-shot composition: ``turns_text`` -> ``{title, summary, ...}``.

    Returns ``None`` on any failure (LLM exception, malformed tool_use,
    empty output). The caller increments ``flush_attempts`` and retries
    up to :data:`PHASE2_MAX_ATTEMPTS`.
    """
    prompt = _phase2_prompt(
        turns_text=turns_text,
        activity_summary=activity_summary,
        is_continuation=is_continuation,
        continues_wikilink=continues_wikilink,
    )
    schema = _phase2_tool_schema()
    if logger is not None:
        logger.emit(
            "llm-prompt",
            call="compose-session-note",
            transcript_id=transcript_id,
            prompt_chars=len(prompt),
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
    if logger is not None:
        logger.emit(
            "llm-response",
            call="compose-session-note",
            transcript_id=transcript_id,
            latency_ms=latency_ms,
            keys=sorted(data.keys()),
        )
    return data


def _phase2_prompt(
    *,
    turns_text: str,
    activity_summary: str,
    is_continuation: bool,
    continues_wikilink: str | None,
) -> str:
    parts: list[str] = [
        "You are composing the human-readable narrative for ONE session "
        "note. The deterministic Activity (commits, issues, files touched) "
        "is already in place; your job is to write the narrative the reader "
        "sees first.",
        "",
        "Return your output via the `compose` tool. Bullet-count caps are "
        f"hard: decisions <= {BULLET_CAPS['decisions']}, worked_on <= "
        f"{BULLET_CAPS['worked_on']}, loose_ends <= {BULLET_CAPS['loose_ends']}. "
        f"Each bullet line <= {BULLET_LINE_MAX} chars.",
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
    parts.extend([
        "",
        "Conversation slice:",
        turns_text,
    ])
    return "\n".join(parts)


def _phase2_tool_schema() -> dict[str, Any]:
    return {
        "name": "compose",
        "description": "Emit the narrative for the session note.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "summary": {"type": "string"},
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
                "loose_ends": {
                    "type": "array",
                    "maxItems": BULLET_CAPS["loose_ends"],
                    "items": {"type": "string", "maxLength": BULLET_LINE_MAX},
                },
            },
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
    """Render the deterministic Activity into prompt-friendly bullets."""
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
    if rb.files_touched:
        lines.append("Files touched: " + ", ".join(rb.files_touched[:30]))
    if rb.plans:
        lines.append("Plans referenced: " + ", ".join(rb.plans))
    if rb.projects:
        lines.append("Projects referenced: " + ", ".join(rb.projects))
    return "\n".join(lines)


def _read_slice_text(
    *,
    sidecar: Sidecar,
    rb: ReplayedBuffer,
    adapter_lookup,
) -> str:
    """Best-effort: reconstruct the conversation text the buffer pointed at.

    Uses the adapter's ``read_slice(from_index)`` API, capped to the
    buffer's slice pointers. Returns an empty string when the adapter
    can't be loaded or the transcript file no longer exists — Phase 2
    falls back to "<no slice>" prompting which still produces a usable
    note from the activity summary alone.
    """
    if not rb.slices:
        return ""
    try:
        adapter: Adapter = adapter_lookup(sidecar.integration)
    except Exception:  # noqa: BLE001
        return ""
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
            return ""
        handle = TranscriptHandle(
            integration=sidecar.integration,
            id=sidecar.transcript_id,
            path=tx_path,
            cwd=Path(sidecar.cwd),
            mtime=datetime.now(_UTC),
        )
    except Exception:  # noqa: BLE001
        return ""
    first_idx = min(s.from_index for s in rb.slices)
    last_idx = max(s.to_index for s in rb.slices)
    out: list[str] = []
    try:
        for turn in adapter.read_slice(handle, from_index=first_idx):
            if turn.index > last_idx:
                break
            text = turn.text or ""
            if not text:
                continue
            out.append(f"[{turn.role}@{turn.index}] {text}")
    except Exception:  # noqa: BLE001 - never crash a flush on adapter failures
        return "\n".join(out)
    return "\n".join(out)


def _phase2_apply(
    *,
    stub_path: Path,
    composed: dict[str, Any],
    wiki_root: Path,
    rb: ReplayedBuffer,
    sidecar: Sidecar,
) -> None:
    """Rewrite the finalised stub with the LLM-composed narrative."""
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

    fm["title"] = title
    fm["description"] = description
    fm["last_reviewed"] = datetime.now(UTC).date().isoformat()
    body = render_body_sections(BodySections(
        title=title,
        summary=summary,
        decisions=_bulletise(decisions),
        worked_on=_bulletise(worked_on),
        loose_ends=_bulletise(loose_ends),
        commits=rb.activity_commits,
        issues_opened=rb.activity_issues_opened,
        issues_closed=rb.activity_issues_closed,
    ))
    new_text = _render_markdown(fm, body, wiki_root=wiki_root)
    atomic_write_text(stub_path, new_text)


# ---------------------------------------------------------------------------
# Top-level entry
# ---------------------------------------------------------------------------


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
    """Drive Phase 1 + (optionally) Phase 2 to completion.

    ``buffer_path`` is the sidecar path (``<stem>.state.json``).
    ``llm_client`` / ``model`` are required for Phase 2; without them
    the worker stops after Phase 1 (Activity-only) and returns.

    Returns a :class:`FlushOutcome` even on partial success — Phase 1
    always runs through to ``state=closed`` once it acquires the lock,
    so callers can rely on the buffer being finalised regardless of
    Phase 2 outcome.
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

    # Phase 1 (deterministic) under flock + state CAS.
    with buffer.with_lock():
        sidecar = buffer.read_sidecar()
        if sidecar is None or sidecar.state == "closed":
            return FlushOutcome(
                buffer_stem=buffer.stem,
                state_before="closed",
                skipped_reason="closed-during-acquire",
            )
        # accumulating -> ready -> flushing (CAS) — accept either entry point.
        if sidecar.state == "accumulating":
            sidecar = buffer.transition("ready")
        if sidecar.state == "ready":
            sidecar = buffer.transition("flushing")
        rb = buffer.replay()
        outcome = _phase1_finalise(
            buffer=buffer, sidecar=sidecar, rb=rb,
            wiki_root=wiki_root, logger=logger,
        )
        # Drain emit (note-filed) before closing.
        if outcome.phase1_completed and outcome.stub_path is not None:
            _drain_emit_filed(
                lore_root=lore_root, sidecar=sidecar, outcome=outcome, logger=logger,
            )
        # Close the buffer regardless of Phase 1 stub presence — handover
        # gates on state=closed, not on stub presence.
        buffer.transition("closed")

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
    turns_text = _read_slice_text(
        sidecar=sidecar, rb=rb_post or rb, adapter_lookup=adapter_lookup,
    )
    activity_summary = _activity_summary_text(rb_post or rb)
    continues_wikilink = (
        f"[[{sidecar.continuation_of}]]"
        if sidecar.part_index >= 2 and sidecar.continuation_of
        else None
    )

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
        _phase2_apply(
            stub_path=outcome.stub_path,
            composed=composed,
            wiki_root=wiki_root,
            rb=rb_post or rb,
            sidecar=sidecar,
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

    outcome.phase2_completed = True
    outcome.composed = composed
    if logger is not None:
        logger.emit(
            "flush-llm-completed",
            transcript_id=sidecar.transcript_id,
            buffer_stem=buffer.stem,
            attempts=attempts,
        )
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
