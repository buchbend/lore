"""One-call chapter composer for lab-notebook session notes.

Per flush, exactly one LLM call turns the buffered transcript slice plus
the complete note-so-far into a chapter of topic blocks: a bold
one-sentence self-sufficient lead, a short prose body, and one ``@turn``
anchor at the block's end. Resumed or corrected topics become
continuation blocks. The note-so-far is sent so a topic left open in an
earlier chapter and resolved now can be picked up as a continuation.

Each turn is seen by an LLM exactly once — there is no cheap outline
pass. ``reasoning_effort`` is not set here; it rides the resolved client
(the high tier stays the generation default) exactly as the request
wrapper configures it.

Two attempts. Between attempts a deterministic **anchor lint** rejects
any anchor outside the chapter's slice, and an injected **publish gate**
may withhold the chapter; either verdict feeds corrective text into the
retry prompt. After two attempts the outcome is surfaced — composed,
withheld (for quarantine downstream), or failed. Appending the chapter,
writing markers, and the give-up/sweep semantics live in their own
layers and consume this outcome; this module never writes to disk.

The gate is dependency-injected. Its contract is small enough to state
here so the scanner/lint/detection implementation can match it:

    gate.evaluate(chapter_text: str) -> GateResult

where ``GateResult`` is either PASS (``GateResult.ok()``) or WITHHELD
(``GateResult.withheld(category, feedback)``). With no gate injected a
:class:`PassThroughGate` is used, so replay tests run standalone.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from lore_core.note_document import Chapter, TopicBlock, render_chapter_body
from lore_core.publish_gate import GateResult

if TYPE_CHECKING:
    from lore_core.run_log import RunLogger

__all__ = [
    "GateResult",
    "Gate",
    "PassThroughGate",
    "ComposeStatus",
    "ComposeResult",
    "compose_chapter",
    "chapter_anchor_lint",
    "chapter_tool_schema",
    "render_chapter_body",
    "CHAPTER_MAX_ATTEMPTS",
    "CHAPTER_MAX_OUTPUT_TOKENS",
]

CHAPTER_MAX_ATTEMPTS = 2
CHAPTER_MAX_OUTPUT_TOKENS = 4000

# Sentinel anchor for a block whose model output carried no usable turn
# index. It can never satisfy the in-slice lint (turn indices are >= 0),
# so a missing anchor is treated exactly like an out-of-slice one.
_ANCHOR_MISSING = -1

_TOOL_NAME = "compose_chapter"


# ---------------------------------------------------------------------------
# Publish-gate seam (interface only — the implementation lives elsewhere)
# ---------------------------------------------------------------------------

# ``GateResult`` is the one canonical verdict type, defined in
# :mod:`lore_core.publish_gate` and re-exported here so the composer's
# retry loop and the gate speak the same type. ``GateResult.ok()`` /
# ``GateResult.withheld(...)`` are its constructors.


@runtime_checkable
class Gate(Protocol):
    """Blocking check between compose and append.

    Implementations scan the rendered chapter for anything unsafe to
    publish and return :class:`GateResult`. This module only reacts to
    the verdict; the deterministic phrasing lint and PII/secret scanners
    live inside the gate.
    """

    def evaluate(self, chapter_text: str) -> GateResult: ...


class PassThroughGate:
    """Default gate: every chapter passes. Lets replay tests run alone."""

    def evaluate(self, chapter_text: str) -> GateResult:  # noqa: ARG002
        return GateResult.ok()


# ---------------------------------------------------------------------------
# Outcome
# ---------------------------------------------------------------------------


class ComposeStatus(Enum):
    COMPOSED = "composed"
    WITHHELD = "withheld"
    FAILED = "failed"


@dataclass
class ComposeResult:
    """Terminal outcome of a chapter compose.

    ``COMPOSED`` carries the ``chapter``. ``WITHHELD`` carries the gate's
    ``category`` / ``feedback`` and the ``withheld_text`` (the rendered
    chapter, for quarantine). ``FAILED`` carries a ``failure_reason``.
    """

    status: ComposeStatus
    chapter: Chapter | None = None
    attempts: int = 0
    withheld_category: str = ""
    withheld_feedback: str = ""
    withheld_text: str = ""
    failure_reason: str = ""


# ---------------------------------------------------------------------------
# Anchor lint (deterministic)
# ---------------------------------------------------------------------------


def chapter_anchor_lint(chapter: Chapter, *, from_turn: int, to_turn: int) -> list[tuple[int, int]]:
    """Return ``(block_index, anchor_turn)`` for anchors outside the slice.

    Every block anchors to exactly one turn; that turn must fall within
    the chapter's ``[from_turn, to_turn]`` slice. A missing anchor
    (sentinel ``-1``) fails the same way. An empty list means the chapter
    is anchor-clean.
    """
    lo, hi = min(from_turn, to_turn), max(from_turn, to_turn)
    offenders: list[tuple[int, int]] = []
    for i, block in enumerate(chapter.blocks):
        turn = block.anchor_turn
        if turn < lo or turn > hi:
            offenders.append((i, turn))
    return offenders


# ---------------------------------------------------------------------------
# Compose loop
# ---------------------------------------------------------------------------


def compose_chapter(
    *,
    slice_text: str,
    slice_from_turn: int,
    slice_to_turn: int,
    note_so_far: str,
    llm_client: Any,
    model: str,
    gate: Gate | None = None,
    logger: RunLogger | None = None,
    transcript_id: str = "",
    max_output_tokens: int = CHAPTER_MAX_OUTPUT_TOKENS,
) -> ComposeResult:
    """Compose one chapter in a single LLM call, with a bounded retry.

    Attempt 1 composes; a deterministic anchor lint and the injected
    gate then judge the result. An anchor-lint miss or a gate withhold
    feeds corrective text into a second attempt. After
    :data:`CHAPTER_MAX_ATTEMPTS` the outcome is returned:

    * PASS → ``COMPOSED`` with the chapter.
    * gate WITHHELD on the final attempt → ``WITHHELD`` (with the
      rendered text for quarantine).
    * no composable / anchor-clean chapter → ``FAILED``.

    A withhold seen on any attempt is preferred over a plain failure as
    the terminal outcome, so the composed-but-unsafe text is preserved
    for the quarantine flow.
    """
    gate = gate or PassThroughGate()
    retry_feedback = ""
    last_withheld: GateResult | None = None
    last_withheld_text = ""
    attempts = 0

    while attempts < CHAPTER_MAX_ATTEMPTS:
        attempts += 1
        chapter = _compose_once(
            slice_text=slice_text,
            slice_from_turn=slice_from_turn,
            slice_to_turn=slice_to_turn,
            note_so_far=note_so_far,
            retry_feedback=retry_feedback,
            llm_client=llm_client,
            model=model,
            max_output_tokens=max_output_tokens,
            logger=logger,
            transcript_id=transcript_id,
        )
        if chapter is None:
            # LLM failure / malformed / empty: nothing structured to
            # correct — retry the same prompt.
            retry_feedback = ""
            continue

        offenders = chapter_anchor_lint(chapter, from_turn=slice_from_turn, to_turn=slice_to_turn)
        if offenders:
            retry_feedback = _anchor_feedback(offenders, slice_from_turn, slice_to_turn)
            if logger is not None:
                logger.emit(
                    "warning",
                    call="chapter-anchor-lint",
                    transcript_id=transcript_id,
                    offenders=offenders,
                )
            continue

        chapter_text = render_chapter_body(chapter)
        verdict = gate.evaluate(chapter_text)
        if verdict.passed:
            return ComposeResult(status=ComposeStatus.COMPOSED, chapter=chapter, attempts=attempts)
        last_withheld = verdict
        last_withheld_text = chapter_text
        retry_feedback = verdict.feedback
        if logger is not None:
            logger.emit(
                "chapter-withheld",
                call="publish-gate",
                transcript_id=transcript_id,
                category=verdict.category,
            )

    if last_withheld is not None:
        return ComposeResult(
            status=ComposeStatus.WITHHELD,
            attempts=attempts,
            withheld_category=last_withheld.category,
            withheld_feedback=last_withheld.feedback,
            withheld_text=last_withheld_text,
        )
    return ComposeResult(
        status=ComposeStatus.FAILED,
        attempts=attempts,
        failure_reason="no anchor-clean chapter composed",
    )


def _anchor_feedback(offenders: list[tuple[int, int]], from_turn: int, to_turn: int) -> str:
    lo, hi = min(from_turn, to_turn), max(from_turn, to_turn)
    bad = ", ".join(str(i) for i, _ in offenders)
    return (
        f"Anchors must resolve within this chapter's turn range {lo}-{hi}. "
        f"The previous attempt cited a turn outside it (block(s) {bad}). "
        f"Every @N anchor must be a turn index shown in the slice, within "
        f"{lo}-{hi}."
    )


# ---------------------------------------------------------------------------
# Single LLM call
# ---------------------------------------------------------------------------


def _compose_once(
    *,
    slice_text: str,
    slice_from_turn: int,
    slice_to_turn: int,
    note_so_far: str,
    retry_feedback: str,
    llm_client: Any,
    model: str,
    max_output_tokens: int,
    logger: RunLogger | None,
    transcript_id: str,
) -> Chapter | None:
    prompt = _build_prompt(
        slice_text=slice_text,
        slice_from_turn=slice_from_turn,
        slice_to_turn=slice_to_turn,
        note_so_far=note_so_far,
        retry_feedback=retry_feedback,
    )
    schema = chapter_tool_schema()
    if logger is not None:
        logger.emit(
            "llm-prompt",
            call="chapter-compose",
            transcript_id=transcript_id,
            prompt_chars=len(prompt),
            slice_chars=len(slice_text),
            note_so_far_chars=len(note_so_far),
            is_retry=bool(retry_feedback),
        )
    t0 = time.monotonic()
    try:
        resp = llm_client.messages.create(
            model=model,
            max_tokens=max_output_tokens,
            tools=[schema],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:  # noqa: BLE001 - never crash a flush on the LLM
        if logger is not None:
            logger.emit(
                "warning",
                call="chapter-compose",
                message=f"LLM call raised: {type(exc).__name__}: {exc}",
            )
        return None
    latency_ms = int((time.monotonic() - t0) * 1000)

    data = _extract_tool_input(resp)
    chapter = _parse_chapter(data)
    if chapter is None:
        if logger is not None:
            logger.emit(
                "warning",
                call="chapter-compose",
                transcript_id=transcript_id,
                message="no usable blocks in response",
            )
        return None
    if logger is not None:
        logger.emit(
            "llm-response",
            call="chapter-compose",
            transcript_id=transcript_id,
            latency_ms=latency_ms,
            block_count=len(chapter.blocks),
            model_resolved=getattr(resp, "model", "") or "",
            reasoning_effort=getattr(resp, "reasoning_effort", None),
        )
    return chapter


def _extract_tool_input(resp: Any) -> dict[str, Any]:
    for block in getattr(resp, "content", []) or []:
        if getattr(block, "type", None) == "tool_use":
            inp = getattr(block, "input", None)
            if isinstance(inp, dict):
                return inp
    return {}


def _parse_chapter(data: dict[str, Any]) -> Chapter | None:
    raw_blocks = data.get("blocks")
    if not isinstance(raw_blocks, list):
        return None
    blocks: list[TopicBlock] = []
    for raw in raw_blocks:
        if not isinstance(raw, dict):
            continue
        lead = str(raw.get("lead") or "").strip()
        body = str(raw.get("body") or "").strip()
        continued = bool(raw.get("continued"))
        continued_topic = str(raw.get("continued_topic") or "").strip()
        anchor = _coerce_anchor(raw.get("anchor"))
        # Drop wholly empty blocks; keep anything with content so the
        # anchor lint can still flag a missing anchor on a real block.
        if not lead and not body and not continued_topic:
            continue
        blocks.append(
            TopicBlock(
                lead=lead,
                body=body,
                anchor_turn=anchor,
                continued=continued,
                continued_topic=continued_topic,
            )
        )
    if not blocks:
        return None
    return Chapter(blocks=blocks)


def _coerce_anchor(value: Any) -> int:
    if isinstance(value, bool):  # bool is an int subclass — reject it
        return _ANCHOR_MISSING
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip().lstrip("@")
        if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
            return int(s)
    return _ANCHOR_MISSING


# ---------------------------------------------------------------------------
# Prompt + tool schema
# ---------------------------------------------------------------------------


def _build_prompt(
    *,
    slice_text: str,
    slice_from_turn: int,
    slice_to_turn: int,
    note_so_far: str,
    retry_feedback: str,
) -> str:
    parts: list[str] = []
    if retry_feedback:
        parts.extend(
            [
                "The previous attempt was rejected before it could be saved.",
                retry_feedback,
                "Re-compose the whole chapter addressing this.",
                "",
            ]
        )
    parts.extend(
        [
            "You are writing ONE chapter of a lab-notebook session note: a "
            "skimmable, chronological record of what a work session discussed "
            "and tried. The chapter is a set of topic blocks.",
            "",
            "Rules:",
            "- Report in the PAST TENSE and a reportive voice. Describe what "
            "was discussed, tried, and left open. No imperatives, no advice, "
            "no TODOs — the note is a record, never a directive.",
            "- Each topic is one block: a bold one-sentence SELF-SUFFICIENT "
            "lead that stands alone (no pronouns reaching into the body — a "
            "reader who sees only the lead must understand it), then a short "
            "prose body.",
            "- End every block with exactly one @N anchor citing the turn "
            f"where that topic started. N is a turn index shown in the slice "
            f"(between {min(slice_from_turn, slice_to_turn)} and "
            f"{max(slice_from_turn, slice_to_turn)}).",
            "- If a topic already in the note so far was resumed or corrected "
            "in this slice, emit a CONTINUATION block: set `continued` and "
            "name the earlier topic in `continued_topic` (the lead renders "
            'as "Continued: <topic>"). Never edit earlier blocks.',
            "- Phrase unsettled or left-open material as prose in the body — "
            "never as a decision, a conclusion, or a directive.",
            "- No kinds, no lead prefixes, no epistemic tags.",
            "",
            "Read the NOTE SO FAR to spot topics left open earlier and "
            "resolved here — those become continuation blocks.",
            "",
            "=== NOTE SO FAR ===",
            note_so_far.strip() if note_so_far.strip() else "(empty — this is the first chapter.)",
            "=== END NOTE SO FAR ===",
            "",
            "TRANSCRIPT SLICE for THIS chapter (each line is [role@N]):",
            "<<<TRANSCRIPT BEGIN>>>",
            slice_text,
            "<<<TRANSCRIPT END>>>",
            "",
            f"Call the `{_TOOL_NAME}` tool exactly once.",
        ]
    )
    return "\n".join(parts)


def chapter_tool_schema() -> dict[str, Any]:
    """Tool schema for the single chapter-compose call."""
    return {
        "name": _TOOL_NAME,
        "description": (
            "Emit the topic blocks for one chapter of a lab-notebook "
            "session note. Each block is a bold self-sufficient lead, a "
            "short prose body, and one @turn anchor."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "blocks": {
                    "type": "array",
                    "description": (
                        "The chapter's topic blocks, in chronological order. One block per topic."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "lead": {
                                "type": "string",
                                "description": (
                                    "One-sentence self-sufficient lead in "
                                    "the past tense. Stands alone; no "
                                    "pronouns into the body. Leave empty "
                                    "only for a continuation block."
                                ),
                            },
                            "body": {
                                "type": "string",
                                "description": (
                                    "Short prose body. Unsettled material "
                                    "phrased as prose, never as a "
                                    "decision or directive."
                                ),
                            },
                            "anchor": {
                                "type": "integer",
                                "description": (
                                    "The turn index where this topic "
                                    "started — must be within the slice."
                                ),
                            },
                            "continued": {
                                "type": "boolean",
                                "description": (
                                    "True when this block resumes or "
                                    "corrects a topic from the note so far."
                                ),
                            },
                            "continued_topic": {
                                "type": "string",
                                "description": (
                                    "For a continuation block, the earlier "
                                    "topic being resumed (renders as "
                                    '"Continued: <topic>").'
                                ),
                            },
                        },
                        "required": ["lead", "anchor"],
                    },
                }
            },
            "additionalProperties": False,
            "required": ["blocks"],
        },
    }
