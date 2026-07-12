"""Typed-fact extraction — the only generative step in a session note.

One first-generation LLM call per logical chunk (chunks come from the
segmenter) reads the raw transcript turns of that chunk and returns TYPED
FACTS: a ``kind``, an optional ``thread`` key, structured ``refs``, a
short ``text``, a ``why`` (mandatory for decisions), and one ``@turn``
anchor. That is the model's whole output surface. It never writes a
quote — quotes are code-attached from the anchor turn — and it never
writes the phrasing that carries authority in the rendered note; code
downstream owns that, keyed on what it could verify.

Calls run sequentially. Call *n* is handed the compact fact table from
calls 1..n-1 for two purposes only: keeping a thread key continuous
across chunks, and not restating what an earlier chunk already recorded.
Facts must come from the transcript chunk, never from the table — no LLM
ever re-reads LLM prose as source material. Two guards make that
structural rather than hopeful: the anchor lint rejects any fact
anchored outside the chunk (which every table entry is), and a
deterministic dedup drops an echo that was re-anchored to slip past it.

Bounded corrective retries mirror the chapter composer's contract:
attempt one extracts, three deterministic lints (anchor in chunk, kind in
enum, decision carries a why) and the injected publish gate judge the
result, and any miss feeds corrective text into exactly one more attempt.

After the final chunk, one bounded call writes a single headline sentence
from the fact table — the only cross-chunk synthesis in the pipeline. A
deterministic lint rejects a headline naming a ref or thread absent from
the table, and a headline that cannot pass it is dropped rather than
published.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from lore_core.note_document import FACT_KINDS, REF_TYPES, Fact, Ref, render_fact_body

from lore_curator.chapter_compose import Gate, PassThroughGate
from lore_curator.chunker import Chunk

if TYPE_CHECKING:
    from lore_core.run_log import RunLogger
    from lore_core.types import Turn

__all__ = [
    "ExtractStatus",
    "ExtractResult",
    "SessionExtraction",
    "chunk_view",
    "compose_headline",
    "decision_why_lint",
    "extract_chunk",
    "extract_session",
    "fact_anchor_lint",
    "fact_kind_lint",
    "fact_table",
    "fact_tool_schema",
    "headline_lint",
    "headline_tool_schema",
    "EXTRACT_MAX_ATTEMPTS",
    "EXTRACT_MAX_OUTPUT_TOKENS",
    "HEADLINE_MAX_ATTEMPTS",
]

# Two attempts, exactly as the chapter composer: one corrective retry on a
# deterministic miss, then the caller degrades instead of looping.
EXTRACT_MAX_ATTEMPTS = 2
EXTRACT_MAX_OUTPUT_TOKENS = 2000

HEADLINE_MAX_ATTEMPTS = 2
HEADLINE_MAX_OUTPUT_TOKENS = 300

# Sentinel for a fact whose payload carried no usable turn index. It can
# never satisfy the in-chunk lint (turn indices are >= 0), so a missing
# anchor is treated exactly like an out-of-chunk one.
_ANCHOR_MISSING = -1

_QUOTE_MAX_CHARS = 240

# Per-turn budget for the extraction view. Richer than the segmenter's,
# because refs live inside tool payloads (a commit sha is in a Bash
# result, a PR number in a `gh` call) — but still bounded, so one pasted
# file cannot push the prompt past a local backend's capacity ceiling.
# ponytail: per-turn clip only; the chunk size band already caps the turn
# count. Add a whole-view budget if a backend starts truncating.
_TURN_MAX_CHARS = 800

_TOOL_NAME = "extract_facts"
_HEADLINE_TOOL_NAME = "write_headline"

_MALFORMED_FEEDBACK = (
    "The previous response carried no `facts` array. Call the tool with "
    "`facts` set to the typed facts of this chunk (an empty array if the "
    "chunk holds nothing worth recording)."
)


class ExtractStatus(Enum):
    EXTRACTED = "extracted"
    EMPTY = "empty"  # the model answered "nothing worth recording"
    WITHHELD = "withheld"
    FAILED = "failed"


@dataclass
class ExtractResult:
    """Terminal outcome of one chunk's extraction.

    ``EXTRACTED`` carries the ``facts``. ``WITHHELD`` carries the gate's
    verdict and the rendered ``withheld_text`` (for quarantine).
    ``FAILED`` carries a ``failure_reason`` — the chunk becomes a coverage
    gap in the note.
    """

    status: ExtractStatus
    chunk: Chunk
    facts: list[Fact] = field(default_factory=list)
    attempts: int = 0
    withheld_category: str = ""
    withheld_feedback: str = ""
    withheld_text: str = ""
    failure_reason: str = ""


@dataclass
class SessionExtraction:
    """Every fact of a session, its headline, and the per-chunk outcomes."""

    facts: list[Fact] = field(default_factory=list)
    headline: str = ""
    results: list[ExtractResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Lints (deterministic)
# ---------------------------------------------------------------------------


def fact_anchor_lint(facts: list[Fact], *, from_turn: int, to_turn: int) -> list[tuple[int, int]]:
    """Return ``(fact_index, anchor)`` for anchors outside the chunk.

    A fact must come from the transcript chunk it was extracted from, and
    its anchor is the proof: an anchor outside ``[from_turn, to_turn]``
    means the fact was not read from these turns — which is exactly the
    shape of a fact copied out of the prior-chunk table. A missing anchor
    (sentinel ``-1``) fails the same way.
    """
    lo, hi = min(from_turn, to_turn), max(from_turn, to_turn)
    return [(i, f.anchor_turn) for i, f in enumerate(facts) if not lo <= f.anchor_turn <= hi]


def fact_kind_lint(facts: list[Fact]) -> list[tuple[int, str]]:
    """Return ``(fact_index, kind)`` for kinds outside the enum."""
    return [(i, f.kind) for i, f in enumerate(facts) if f.kind not in FACT_KINDS]


def decision_why_lint(facts: list[Fact]) -> list[int]:
    """Return the indices of ``decision`` facts carrying no ``why``.

    A decision without its reason is the most poison-prone line in a note:
    later sessions read it as settled and cannot check the reasoning. It
    is the one field the model must not omit.
    """
    return [i for i, f in enumerate(facts) if f.kind == "decision" and not f.why.strip()]


# A headline may only name what the fact table licensed. Checked token
# shapes: `#123` refs, commit-shaped hex, `backticked` spans, and
# path-shaped tokens — the forms that carry authority and can be
# hallucinated. ponytail: plain unquoted prose is not checked; a headline
# naming a thread in bare words slips through. Tighten only if it happens.
_HEADLINE_TOKENS = (
    re.compile(r"#\d+"),
    re.compile(r"`[^`]+`"),
    re.compile(r"\b[0-9a-f]{7,40}\b"),
    re.compile(r"\S*/\S*"),
)


def headline_lint(headline: str, facts: list[Fact]) -> str:
    """Return corrective feedback for a headline over-reaching the table.

    The fact table is the headline's entire licensed source, so any ref,
    identifier, or path it names must appear there. Clean returns ``""``.
    """
    licensed = fact_table(facts).lower()
    offenders: list[str] = []
    for pattern in _HEADLINE_TOKENS:
        for raw in pattern.findall(headline):
            needle = raw.strip("`#").lower()
            if not needle or (pattern.pattern.startswith(r"\b[0-9a-f]") and not _has_digit(needle)):
                continue
            if needle not in licensed and raw not in offenders:
                offenders.append(raw)
    if not offenders:
        return ""
    shown = ", ".join(offenders)
    return (
        f"The headline names {shown}, which the fact table does not contain. "
        "Write the headline using only what the table records — never a "
        "reference, file, or name that is not in it."
    )


def _has_digit(text: str) -> bool:
    return any(c.isdigit() for c in text)


# ---------------------------------------------------------------------------
# The fact table (the only thing carried forward between calls)
# ---------------------------------------------------------------------------


def fact_table(facts: list[Fact]) -> str:
    """Compact rendering of the facts recorded so far.

    Carried into the next extraction call for thread continuity and dedup,
    and into the headline call as its only source. Deliberately terse:
    it is context, never material to extract from.
    """
    lines: list[str] = []
    for f in facts:
        thread = f"[{f.thread}] " if f.thread else ""
        refs = " (" + ", ".join(f"{r.type}:{r.value}" for r in f.refs) + ")" if f.refs else ""
        lines.append(f"{thread}{f.kind} @{f.anchor_turn} — {f.text}{refs}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The transcript view one extraction call reads
# ---------------------------------------------------------------------------


def chunk_view(turns: list[Turn]) -> str:
    """Render a chunk's turns for extraction.

    Unlike the segmenter's collapsed view, tool payloads are KEPT: a
    `done` fact's ref — a commit sha, a merged PR number — exists nowhere
    else in the transcript. Each turn is clipped, so a pasted file bounds
    its own cost.
    """
    lines: list[str] = []
    for t in turns:
        rendered = _render_turn(t)
        if rendered:
            lines.append(f"[{t.role}@{t.index}] {rendered}")
    return "\n".join(lines)


def _render_turn(turn: Turn) -> str:
    if turn.tool_call is not None:
        payload = json.dumps(turn.tool_call.input or {}, ensure_ascii=False)
        return f"<tool: {turn.tool_call.name}> {_clip(payload)}"
    if turn.tool_result is not None:
        flag = " error" if turn.tool_result.is_error else ""
        return f"<result{flag}> {_clip(turn.tool_result.output or '')}"
    return _clip((turn.text or "").strip())


def _clip(text: str, limit: int = _TURN_MAX_CHARS) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _quote_source(turns: list[Turn]) -> dict[int, str]:
    """Verbatim quotable text per turn index — the code-attached quotes."""
    return {t.index: _clip(_render_turn(t), _QUOTE_MAX_CHARS) for t in turns}


def _attach_quotes(facts: list[Fact], quotes: dict[int, str]) -> None:
    """Fill each fact's quote from its anchor turn. The model never writes one.

    Called after the anchor lint passes, so every anchor is a valid key,
    and before the facts are rendered for the gate, so the same single
    scan covers the quote too.
    """
    for f in facts:
        f.quote = quotes.get(f.anchor_turn, "")


# ---------------------------------------------------------------------------
# One chunk
# ---------------------------------------------------------------------------


def extract_chunk(
    *,
    chunk: Chunk,
    turns: list[Turn],
    prior_facts: list[Fact] | None = None,
    llm_client: Any,
    model: str,
    gate: Gate | None = None,
    logger: RunLogger | None = None,
    transcript_id: str = "",
) -> ExtractResult:
    """Extract one chunk's typed facts in a single LLM call, with one retry.

    ``turns`` is the whole replayed session; the chunk's own span is cut
    from it here. ``prior_facts`` are the facts of chunks 1..n-1 — context
    for thread keys and dedup, never material to extract from.

    * lint-clean and gate-passed → ``EXTRACTED``.
    * zero facts (or only echoes of the table) → ``EMPTY``: the model
      answered "nothing worth recording", which is never retried.
    * gate withheld on the final attempt → ``WITHHELD``, carrying the
      rendered text for quarantine.
    * still failing a lint after the retry → ``FAILED``; the chunk becomes
      a coverage gap rather than a note full of unusable facts. ponytail:
      whole-chunk failure, not per-fact salvage — revisit if weak local
      models turn out to miss one lint out of many good facts.
    """
    gate = gate or PassThroughGate()
    prior = list(prior_facts or [])
    span = [t for t in turns if chunk.from_turn <= t.index <= chunk.to_turn]
    quotes = _quote_source(span)
    seen = {_norm(f.text) for f in prior}

    retry_feedback = ""
    last_withheld_category = ""
    last_withheld_feedback = ""
    last_withheld_text = ""
    attempts = 0

    while attempts < EXTRACT_MAX_ATTEMPTS:
        attempts += 1
        facts = _extract_once(
            span=span,
            chunk=chunk,
            prior=prior,
            retry_feedback=retry_feedback,
            llm_client=llm_client,
            model=model,
            logger=logger,
            transcript_id=transcript_id,
        )
        if facts is None:
            # LLM failure / malformed: nothing structured to correct.
            retry_feedback = _MALFORMED_FEEDBACK
            continue
        if not facts:
            return ExtractResult(status=ExtractStatus.EMPTY, chunk=chunk, attempts=attempts)

        feedback = _lint_feedback(facts, chunk)
        if feedback:
            retry_feedback = feedback
            if logger is not None:
                logger.emit(
                    "warning",
                    call="fact-lint",
                    transcript_id=transcript_id,
                    chunk=[chunk.from_turn, chunk.to_turn],
                    message=feedback,
                )
            continue

        # The table is context, not source material: an entry echoed back
        # (re-anchored into this chunk, so the anchor lint let it through)
        # is dropped, never appended a second time.
        facts = [f for f in facts if _norm(f.text) not in seen]
        if not facts:
            return ExtractResult(status=ExtractStatus.EMPTY, chunk=chunk, attempts=attempts)

        _attach_quotes(facts, quotes)
        text = render_fact_body(facts)
        verdict = gate.evaluate(text)
        if verdict.passed:
            return ExtractResult(
                status=ExtractStatus.EXTRACTED, chunk=chunk, facts=facts, attempts=attempts
            )
        last_withheld_category = verdict.category
        last_withheld_feedback = verdict.feedback
        last_withheld_text = text
        retry_feedback = verdict.feedback
        if logger is not None:
            logger.emit(
                "chapter-withheld",
                call="publish-gate",
                transcript_id=transcript_id,
                category=verdict.category,
            )

    if last_withheld_text:
        return ExtractResult(
            status=ExtractStatus.WITHHELD,
            chunk=chunk,
            attempts=attempts,
            withheld_category=last_withheld_category,
            withheld_feedback=last_withheld_feedback,
            withheld_text=last_withheld_text,
        )
    return ExtractResult(
        status=ExtractStatus.FAILED,
        chunk=chunk,
        attempts=attempts,
        failure_reason="no lint-clean facts extracted",
    )


def _norm(text: str) -> str:
    return " ".join(text.lower().split()).strip(" .")


def _lint_feedback(facts: list[Fact], chunk: Chunk) -> str:
    """All three lints at once — one retry corrects every miss it names."""
    parts: list[str] = []
    lo, hi = chunk.from_turn, chunk.to_turn
    bad_anchors = fact_anchor_lint(facts, from_turn=lo, to_turn=hi)
    if bad_anchors:
        shown = ", ".join(str(a) for _, a in bad_anchors)
        parts.append(
            f"Every fact must be anchored to a turn of THIS chunk, in the "
            f"range {lo}-{hi}. These anchors were outside it: {shown}. A fact "
            f"you cannot anchor inside {lo}-{hi} does not belong in this "
            f"chunk — drop it."
        )
    bad_kinds = fact_kind_lint(facts)
    if bad_kinds:
        shown = ", ".join(repr(k) for _, k in bad_kinds)
        parts.append(f"`kind` must be one of {', '.join(FACT_KINDS)}. These were not: {shown}.")
    why_less = decision_why_lint(facts)
    if why_less:
        parts.append(
            f"A `decision` fact must carry its `why` — the reason the choice "
            f"was made. {len(why_less)} decision(s) had none. Give the reason, "
            f"or record it as a different kind."
        )
    return " ".join(parts)


def _extract_once(
    *,
    span: list[Turn],
    chunk: Chunk,
    prior: list[Fact],
    retry_feedback: str,
    llm_client: Any,
    model: str,
    logger: RunLogger | None,
    transcript_id: str,
) -> list[Fact] | None:
    """One call. ``None`` means malformed or failed (retryable)."""
    prompt = _build_prompt(span=span, chunk=chunk, prior=prior, retry_feedback=retry_feedback)
    if logger is not None:
        logger.emit(
            "llm-prompt",
            call="fact-extract",
            transcript_id=transcript_id,
            prompt_chars=len(prompt),
            chunk=[chunk.from_turn, chunk.to_turn],
            is_retry=bool(retry_feedback),
        )
    t0 = time.monotonic()
    try:
        resp = llm_client.messages.create(
            model=model,
            max_tokens=EXTRACT_MAX_OUTPUT_TOKENS,
            tools=[fact_tool_schema()],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:  # noqa: BLE001 - never crash a close on the LLM
        if logger is not None:
            logger.emit(
                "warning",
                call="fact-extract",
                transcript_id=transcript_id,
                message=f"LLM call raised: {type(exc).__name__}: {exc}",
            )
        return None
    latency_ms = int((time.monotonic() - t0) * 1000)

    raw = _tool_input(resp).get("facts")
    if not isinstance(raw, list):
        return None
    facts = _parse_facts(raw)
    if logger is not None:
        logger.emit(
            "llm-response",
            call="fact-extract",
            transcript_id=transcript_id,
            latency_ms=latency_ms,
            fact_count=len(facts),
            model_resolved=getattr(resp, "model", "") or "",
        )
    return facts


def _tool_input(resp: Any) -> dict[str, Any]:
    for block in getattr(resp, "content", []) or []:
        if getattr(block, "type", None) == "tool_use":
            inp = getattr(block, "input", None)
            if isinstance(inp, dict):
                return inp
    return {}


def _parse_facts(raw: list[Any]) -> list[Fact]:
    """Parse the tool payload into facts. Values are NOT corrected here.

    A bogus kind or anchor round-trips as it was sent, so the lints judge
    the model's actual output instead of a quietly repaired version of it.
    Any `quote` the model sent is ignored: quotes are code-attached.
    """
    facts: list[Fact] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        facts.append(
            Fact(
                kind=str(item.get("kind") or "").strip(),
                text=text,
                anchor_turn=_coerce_anchor(item.get("anchor")),
                thread=str(item.get("thread") or "").strip(),
                refs=_parse_refs(item.get("refs")),
                why=str(item.get("why") or "").strip(),
            )
        )
    return facts


def _parse_refs(raw: Any) -> list[Ref]:
    if not isinstance(raw, list):
        return []
    refs: list[Ref] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        rtype = str(item.get("type") or "").strip()
        value = str(item.get("value") or "").strip()
        if rtype in REF_TYPES and value:
            refs.append(Ref(rtype, value))
    return refs


def _coerce_anchor(value: Any) -> int:
    if isinstance(value, bool):  # bool is an int subclass — never an index
        return _ANCHOR_MISSING
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip().lstrip("@")
        if s.isdigit():
            return int(s)
    return _ANCHOR_MISSING


# ---------------------------------------------------------------------------
# The whole session
# ---------------------------------------------------------------------------


def extract_session(
    *,
    chunks: list[Chunk],
    turns: list[Turn],
    llm_client: Any,
    model: str,
    gate: Gate | None = None,
    logger: RunLogger | None = None,
    transcript_id: str = "",
    headline: bool = True,
) -> SessionExtraction:
    """Extract every chunk in order, then write one headline from the table.

    Sequential by design: each call is handed the facts recorded so far so
    a thread key stays continuous across chunk boundaries and nothing is
    restated. A chunk that fails or is withheld does not stop the ones
    after it — its outcome rides along in ``results`` for the caller to
    record as a coverage gap.
    """
    facts: list[Fact] = []
    results: list[ExtractResult] = []
    for chunk in chunks:
        result = extract_chunk(
            chunk=chunk,
            turns=turns,
            prior_facts=facts,
            llm_client=llm_client,
            model=model,
            gate=gate,
            logger=logger,
            transcript_id=transcript_id,
        )
        results.append(result)
        facts.extend(result.facts)

    line = ""
    if headline and facts:
        line = compose_headline(
            facts,
            llm_client=llm_client,
            model=model,
            gate=gate,
            logger=logger,
            transcript_id=transcript_id,
        )
    return SessionExtraction(facts=facts, headline=line, results=results)


def compose_headline(
    facts: list[Fact],
    *,
    llm_client: Any,
    model: str,
    gate: Gate | None = None,
    logger: RunLogger | None = None,
    transcript_id: str = "",
) -> str:
    """One bounded call: a single sentence written from the fact table.

    The one cross-chunk synthesis in the pipeline, and the one place a
    model may generalise. It is fenced in accordingly: the table is its
    only source, a deterministic lint rejects any ref or name absent from
    it, and a headline that still over-reaches after its one corrective
    retry is dropped — a note with no headline beats a headline with a
    ref that does not exist.
    """
    gate = gate or PassThroughGate()
    retry_feedback = ""
    for _ in range(HEADLINE_MAX_ATTEMPTS):
        prompt = _build_headline_prompt(facts, retry_feedback=retry_feedback)
        try:
            resp = llm_client.messages.create(
                model=model,
                max_tokens=HEADLINE_MAX_OUTPUT_TOKENS,
                tools=[headline_tool_schema()],
                tool_choice={"type": "tool", "name": _HEADLINE_TOOL_NAME},
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception:  # noqa: BLE001 - a headline never blocks the note
            continue
        line = str(_tool_input(resp).get("headline") or "").strip()
        if not line:
            continue
        retry_feedback = headline_lint(line, facts)
        if retry_feedback:
            if logger is not None:
                logger.emit(
                    "warning",
                    call="headline-lint",
                    transcript_id=transcript_id,
                    message=retry_feedback,
                )
            continue
        verdict = gate.evaluate(line)
        if verdict.passed:
            return line
        retry_feedback = verdict.feedback
    return ""


# ---------------------------------------------------------------------------
# Prompts + tool schemas
# ---------------------------------------------------------------------------


def _build_prompt(*, span: list[Turn], chunk: Chunk, prior: list[Fact], retry_feedback: str) -> str:
    lo, hi = chunk.from_turn, chunk.to_turn
    parts: list[str] = []
    if retry_feedback:
        parts.extend(
            [
                "The previous attempt was rejected.",
                retry_feedback,
                "Extract the facts again, corrected.",
                "",
            ]
        )
    parts.extend(
        [
            "Extract the FACTS of one chunk of a work session. A colleague "
            "reads them months later to learn what the work established.",
            "",
            "Record the WORK, not the WORKING. The subject is the problem, "
            "the system, and what was settled about it — never the session "
            "itself. Session mechanics are noise: greetings, slash commands, "
            "tool and sandbox hiccups, permission chatter. Leave them out "
            "unless the tooling was itself the subject of the work.",
            "",
            "THE MONTH TEST. A fact earns its place only if it would change "
            "what a colleague does or believes a month from now. Everything "
            "else — however busy the session was — is left out. Few facts, or "
            "none, is the normal answer for a chunk.",
            "",
            "THE TERMINAL-STATE RULE. `done` is for a TERMINAL state and "
            "nothing else: a commit landed, a PR merged, a test suite "
            "verified green, an artefact released. Work en route to that — an "
            "edit made, a file written, a draft opened, a review requested — "
            "is `progress`, even when it felt like a milestone at the time. "
            "If you cannot point at the turn where the thing actually "
            "concluded, it is not `done`.",
            "",
            "THE SUPERVISION CLAUSE. When this session supervises other "
            "agents, the subject is the DELIVERABLE, not the choreography. "
            "Dispatching a teammate, posting a status comment, preparing a "
            "worktree — none of that is a fact. What the teammate delivered, "
            "and whether it landed, is.",
            "",
            "Quoted and reference material is NOT the session's work. When "
            "the user pastes a block as an exemplar, a reference, or a "
            "comparison — a formatting sample, an older note of their own, an "
            "example to imitate — its content describes that material, not "
            "this session. Never report the claims, tools, or paths inside it "
            "as facts of this session. The exception is when working on that "
            "material WAS the task.",
            "",
            "Each fact carries:",
            "- `kind`: one of "
            "`progress` (work en route), "
            "`done` (a terminal state, per the rule above), "
            "`decision` (a choice made — `why` is MANDATORY), "
            "`finding` (something learned about the system), "
            "`open` (something unresolved, stated as a fact, never as an "
            "instruction or a TODO).",
            "- `thread`: a short stable key for the line of work this fact "
            "belongs to (e.g. `chunker`, `auth-refactor`). Reuse the SAME key "
            "when a fact continues a thread already in the table below.",
            "- `refs`: the checkable pointers — pr, commit, file, tag, issue. "
            "Take them verbatim from the transcript (a sha from a commit "
            "result, a number from a `gh` call). NEVER invent or guess one: a "
            "ref that does not exist is worse than no ref.",
            "- `text`: one short, self-sufficient sentence. Plain statement of "
            "what is so — no hedging, no flourish, no markdown.",
            "- `why`: the reason behind a `decision`.",
            f"- `anchor`: the ONE turn index this fact came from, between {lo} and {hi}.",
            "",
        ]
    )
    if prior:
        parts.extend(
            [
                "FACTS ALREADY RECORDED (earlier chunks of this session). This "
                "table is CONTEXT ONLY, for two purposes: reuse a `thread` key "
                "it already established, and do not restate what it already "
                "records. It is NOT source material — never extract a fact "
                "from it. Every fact you emit must come from the transcript "
                "below and be anchored in it.",
                "<<<TABLE BEGIN>>>",
                fact_table(prior),
                "<<<TABLE END>>>",
                "",
            ]
        )
    parts.extend(
        [
            f"TRANSCRIPT CHUNK, turns {lo}-{hi} (each line is [role@N]):",
            "<<<TRANSCRIPT BEGIN>>>",
            chunk_view(span),
            "<<<TRANSCRIPT END>>>",
            "",
            "If this chunk holds nothing that passes the month test, return "
            "an EMPTY facts array. No fact is better than a noisy one.",
            "",
            f"Call the `{_TOOL_NAME}` tool exactly once.",
        ]
    )
    return "\n".join(parts)


def _build_headline_prompt(facts: list[Fact], *, retry_feedback: str) -> str:
    parts: list[str] = []
    if retry_feedback:
        parts.extend(
            [
                "The previous attempt was rejected.",
                retry_feedback,
                "Write the headline again, corrected.",
                "",
            ]
        )
    parts.extend(
        [
            "Write ONE sentence naming what this work session actually "
            "achieved — the thing a colleague would say if asked what came of "
            "it. Prefer the terminal outcomes (`done`) over the work en route "
            "(`progress`); when several threads closed, name what they add up "
            "to rather than listing them.",
            "",
            "The fact table below is your ONLY source. Do not name a "
            "reference, file, number, or identifier that is not in it — a "
            "detail you add from imagination is a false claim. Plain "
            "statement, no markdown, no hedging.",
            "",
            "<<<TABLE BEGIN>>>",
            fact_table(facts),
            "<<<TABLE END>>>",
            "",
            f"Call the `{_HEADLINE_TOOL_NAME}` tool exactly once.",
        ]
    )
    return "\n".join(parts)


def fact_tool_schema() -> dict[str, Any]:
    """Tool schema for one chunk's extraction — typed data, no prose.

    There is deliberately no `quote` field: quotes are code-attached from
    the anchor turn, so a model cannot author the verbatim evidence for
    its own claim.
    """
    return {
        "name": _TOOL_NAME,
        "description": (
            "Emit the typed facts of one chunk of a work session. An empty "
            "facts array is the correct output when the chunk holds nothing "
            "worth recording a month from now."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "facts": {
                    "type": "array",
                    "description": (
                        "The chunk's facts, in chronological order. Few or none is normal."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "kind": {
                                "type": "string",
                                "enum": list(FACT_KINDS),
                                "description": (
                                    "progress = work en route; done = a "
                                    "terminal state (commit landed, PR "
                                    "merged, suite verified green); decision "
                                    "= a choice made (why is mandatory); "
                                    "finding = something learned; open = "
                                    "something unresolved."
                                ),
                            },
                            "text": {
                                "type": "string",
                                "description": (
                                    "One short self-sufficient sentence "
                                    "stating what is so. Plain text."
                                ),
                            },
                            "thread": {
                                "type": "string",
                                "description": (
                                    "Short stable key for the line of work "
                                    "this fact belongs to. Reuse the key an "
                                    "earlier chunk established."
                                ),
                            },
                            "refs": {
                                "type": "array",
                                "description": (
                                    "Checkable pointers, taken verbatim from "
                                    "the transcript. Never invented."
                                ),
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "type": {
                                            "type": "string",
                                            "enum": list(REF_TYPES),
                                        },
                                        "value": {
                                            "type": "string",
                                            "description": (
                                                "The PR/issue number, commit "
                                                "sha, tag, or file path."
                                            ),
                                        },
                                    },
                                    "required": ["type", "value"],
                                },
                            },
                            "why": {
                                "type": "string",
                                "description": (
                                    "The reason behind a decision — mandatory "
                                    "for kind=decision, omitted otherwise."
                                ),
                            },
                            "anchor": {
                                "type": "integer",
                                "description": (
                                    "The one turn index this fact came from. "
                                    "Must be inside this chunk."
                                ),
                            },
                        },
                        "required": ["kind", "text", "anchor"],
                    },
                }
            },
            "additionalProperties": False,
            "required": ["facts"],
        },
    }


def headline_tool_schema() -> dict[str, Any]:
    """Tool schema for the single headline call."""
    return {
        "name": _HEADLINE_TOOL_NAME,
        "description": (
            "Emit one sentence naming what the session achieved, using only "
            "what the fact table records."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "headline": {
                    "type": "string",
                    "description": (
                        "One plain sentence. Names no reference, file, or "
                        "identifier absent from the fact table."
                    ),
                }
            },
            "additionalProperties": False,
            "required": ["headline"],
        },
    }
