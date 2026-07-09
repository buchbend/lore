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
retry prompt. A model that returns zero blocks has *answered* — nothing
of substance in the slice — which is the terminal ``EMPTY`` outcome,
never retried and never gated (there is no text). After two attempts the
outcome is surfaced — composed, empty, withheld (for quarantine
downstream), or failed. Appending the chapter, writing markers, and the
give-up/sweep semantics live in their own layers and consume this
outcome; this module never writes to disk.

The gate is dependency-injected. Its contract is small enough to state
here so the scanner/detection implementation can match it:

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
    "chapter_same_anchor_lint",
    "chapter_tool_schema",
    "render_chapter_body",
    "CHAPTER_MAX_ATTEMPTS",
    "CHAPTER_MAX_OUTPUT_TOKENS",
]

CHAPTER_MAX_ATTEMPTS = 2
CHAPTER_MAX_OUTPUT_TOKENS = 4000
_QUOTE_MAX_CHARS = 240

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
    the verdict; the PII/secret scanners and detection live inside the
    gate.
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
    EMPTY = "empty"  # the model answered "nothing of substance"
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


def chapter_same_anchor_lint(chapter: Chapter) -> int | None:
    """Return the anchor turn shared by more than two blocks, else ``None``.

    Two blocks citing the same turn is common and unremarkable — two
    closely related findings surfacing together. More than two is usually
    a tell that every block was derived from one quoted or pasted passage
    rather than distinct moments in the session, which collapses the
    anchors and makes them useless for navigation. This is a soft signal:
    callers retry once for a nudge, never reject on it.
    """
    counts: dict[int, int] = {}
    for block in chapter.blocks:
        counts[block.anchor_turn] = counts.get(block.anchor_turn, 0) + 1
    for anchor, count in counts.items():
        if count > 2:
            return anchor
    return None


def _attach_quotes(chapter: Chapter, turns_by_index: dict[int, str] | None) -> None:
    """Fill each block's verbatim quote from its anchor turn's text.

    Called after anchor lint passes, so every ``anchor_turn`` is a valid
    key into ``turns_by_index`` — and before the chapter is rendered, so
    the same single gate scan covers the quote too. Deterministic given
    the same (chapter, turns_by_index); truncated to a fixed length so
    one long paste can't dominate the note.
    """
    if not turns_by_index:
        return
    for block in chapter.blocks:
        text = (turns_by_index.get(block.anchor_turn) or "").strip()
        if not text:
            continue
        if len(text) > _QUOTE_MAX_CHARS:
            text = text[:_QUOTE_MAX_CHARS].rstrip() + "…"
        block.quote = text


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
    turns_by_index: dict[int, str] | None = None,
) -> ComposeResult:
    """Compose one chapter in a single LLM call, with a bounded retry.

    ``turns_by_index`` maps turn index to raw transcript text; when given,
    each block's verbatim ``quote`` is code-attached from its anchor
    turn after the anchor lint passes, before the chapter is rendered
    for the gate. The model never writes this text.

    Attempt 1 composes; a deterministic anchor lint and the injected
    gate then judge the result. An anchor-lint miss or a gate withhold
    feeds corrective text into a second attempt. A softer same-anchor
    lint (more than two blocks citing one turn) earns at most one
    corrective retry of its own but never blocks publication — after
    that single nudge the chapter is composed regardless of whether the
    anchors changed. After :data:`CHAPTER_MAX_ATTEMPTS` the outcome is
    returned:

    * PASS → ``COMPOSED`` with the chapter.
    * zero blocks on any attempt → ``EMPTY`` immediately (the model
      answered "nothing of substance"; there is no text to lint or gate
      and a retry would only pressure it into manufacturing noise).
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
    same_anchor_retried = False

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
            # LLM failure / malformed: nothing structured to correct —
            # retry the same prompt.
            retry_feedback = ""
            continue
        if not chapter.blocks:
            return ComposeResult(status=ComposeStatus.EMPTY, attempts=attempts)

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

        shared_anchor = chapter_same_anchor_lint(chapter)
        retry_left = attempts < CHAPTER_MAX_ATTEMPTS
        if shared_anchor is not None and not same_anchor_retried and retry_left:
            same_anchor_retried = True
            retry_feedback = _same_anchor_feedback(shared_anchor)
            if logger is not None:
                logger.emit(
                    "warning",
                    call="chapter-same-anchor-lint",
                    transcript_id=transcript_id,
                    anchor=shared_anchor,
                )
            continue

        _attach_quotes(chapter, turns_by_index)
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


def _same_anchor_feedback(anchor: int) -> str:
    return (
        f"Several blocks all cite the same turn (@{anchor}). Confirm these "
        "are distinct findings from the session's progression, not "
        "restatements of one quoted or pasted passage — re-anchor each "
        "block to the turn where its own topic actually started."
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
                message="malformed response (no blocks array)",
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
    """Parse the tool payload; ``None`` means malformed (retryable).

    An empty (or wholly contentless) ``blocks`` list is NOT malformed:
    it is the model's "nothing of substance" answer and round-trips as a
    chapter with zero blocks, which the compose loop maps to ``EMPTY``.
    """
    raw_blocks = data.get("blocks")
    if not isinstance(raw_blocks, list):
        return None
    blocks: list[TopicBlock] = []
    for raw in raw_blocks:
        if not isinstance(raw, dict):
            continue
        # The renderer bolds the lead itself; embedded ** from the model
        # would render as "****lead****", so strip markdown bold here.
        lead = str(raw.get("lead") or "").replace("**", "").strip()
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
    lo = min(slice_from_turn, slice_to_turn)
    hi = max(slice_from_turn, slice_to_turn)
    parts.extend(
        [
            "You are distilling ONE chapter of a lab-notebook session note "
            "from a work-session transcript. A colleague reads the note "
            "months later to learn what the session figured out.",
            "",
            "Record the WORK, not the WORKING. The subject is the problem, "
            "the system, and what was learned about it — never the session "
            "itself. Session mechanics are noise: greetings, test messages, "
            "slash commands, tool/sandbox/environment hiccups, plugin and "
            "permission chatter. Leave them out entirely, unless diagnosing "
            "the tooling was itself the session's purpose.",
            "",
            "Quoted and reference material is NOT the session's work. "
            "Sometimes the user pastes or quotes a block as an exemplar, a "
            "reference, or a comparison, not as something to act on: a "
            'formatting sample ("this is a form I\'d like the notes to have", '
            '"here is the shape I want"), an example ("for example", "like '
            'this one"), or another / older artefact of their own ("an older '
            'version of", "the previous note", "a note I wrote earlier"). Its '
            "content is ABOUT that pasted material and does not describe what "
            "this session did. Spot it by the exemplar framing in the "
            "surrounding turns, by a fenced code block, or by a block that "
            "carries its OWN @N-anchored bold leads (the shape of an earlier "
            "note). NEVER report the claims, tools, paths, or config inside "
            "such material as this session's findings. The ONLY exception is "
            "when working on that material was itself the topic: the user asks "
            "you to review, fix, or reason about the pasted content, in which "
            "case that work IS the session's work and you record it.",
            "",
            "Each topic is ONE block:",
            "- The lead is one bold sentence carrying the takeaway: a "
            "SELF-SUFFICIENT declarative claim a reader understands with no "
            "other context (no pronouns reaching into the body). Three "
            "shapes:",
            '  * a finding — something figured out: "Host compromise '
            'dominates service-auth risk on co-located hosts."',
            '  * an outcome — something tried, with its result: "Bounding '
            "the startup sweep to eight composes cut startup from a "
            'two-minute timeout to seconds."',
            "  * a gap — something left open, stated as a fact about the "
            'work, never as an instruction: "The deployment doc still '
            "lacks a trust-boundary section, so adopters won't know the "
            'secret lands as plaintext on the host."',
            "- The body is short prose carrying the reasoning and the "
            "specifics (names, numbers, paths) behind the lead. Active "
            "voice; name the actor or system. Every sentence must add "
            "information.",
            "- End every block with exactly one @N anchor citing the turn "
            f"where that topic started. N is a turn index shown in the "
            f"slice (between {lo} and {hi}).",
            "",
            "Write FEW blocks — only what has substance. Never narrate "
            'events ("The session was started", "A command was executed") '
            "and never emit directives or TODOs. If the slice contains "
            "nothing of substance, return an EMPTY blocks array — no note "
            "is better than a noisy one.",
            "",
            "If a topic already in the note so far was resumed or corrected "
            "in this slice, emit a CONTINUATION block: set `continued` and "
            "name the earlier topic in `continued_topic` (the lead renders "
            'as "Continued: <topic>"). Never edit earlier blocks. Read the '
            "NOTE SO FAR to spot topics left open earlier and resolved "
            "here — those become continuation blocks.",
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
            "short prose body, and one @turn anchor. An empty blocks "
            "array is the correct output when the slice contains nothing "
            "of substance."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "blocks": {
                    "type": "array",
                    "description": (
                        "The chapter's topic blocks, in chronological "
                        "order. One block per topic with substance; "
                        "empty when there is none."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "lead": {
                                "type": "string",
                                "description": (
                                    "One-sentence self-sufficient "
                                    "declarative claim: a finding, an "
                                    "outcome, or a gap stated as a fact. "
                                    "Plain text — no markdown, the "
                                    "renderer bolds it. Stands alone; no "
                                    "pronouns into the body. Leave empty "
                                    "only for a continuation block."
                                ),
                            },
                            "body": {
                                "type": "string",
                                "description": (
                                    "Short prose body: the reasoning and "
                                    "specifics behind the lead, in the "
                                    "active voice. No event narration. "
                                    "Never restate claims from material "
                                    "the user only pasted or quoted as an "
                                    "exemplar or reference."
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
