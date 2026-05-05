"""Deterministic prefilter for narrative-shape decisions.

Pure-regex pass over user-turn text — no LLM, no external state. Drives
the gate that decides whether a session note may render a ``Decisions``
section, an ADR-cue marker, or must fall through to discussion-shape.

Three coarse signals are extracted:

- ``no_edit_intent`` — the user said "no code change" / "just brainstorm" /
  "exploring" somewhere in the slice. A single hit is enough; this is a
  session-level override that defeats any later assent.
- ``assent_hits`` — per-sentence hits where the user appears to commit
  ("yes", "let's go with X", "approved", "decided") or override a model
  suggestion ("no, do X instead"). Each hit carries a confidence: hits
  in a sentence that also contains a hedge ("maybe", "could", "might")
  are downgraded to ``weak``; otherwise ``strong``.
- ``adr_flagged`` — explicit user cue to record an ADR ("ADR this", "let's
  write an ADR"). No regex-induced vault mutation: the gate just surfaces
  the boolean for the renderer to act on.

Why a per-sentence hedge check, not a per-turn one: the user often
strings "we could do X — let's go with X" where the hedge applies to a
prior consideration and the assent is the operative half. Suppressing
turn-wide on hedge would discard genuine commitments. Sentence-scope
keeps the strong/weak distinction honest.

Why bare regex (no LLM): this layer must be cacheable, deterministic,
and cheap enough to run on every flush. The downstream ``decisions_allowed``
gate combines these signals with ``files_modified`` count to decide
narrative shape; the LLM-judge stage that resolves ambiguous candidates
is intentionally deferred (see plan ``yes-do-that-keen-yeti`` step-7
out-of-scope notes).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from lore_core.types import Turn


__all__ = [
    "AssentHit",
    "PrefilterSignals",
    "extract_signals",
    "prior_assistant_text",
]


AssentKind = Literal["assent", "override"]
Confidence = Literal["strong", "weak"]


# ---------------------------------------------------------------------------
# Regex banks
# ---------------------------------------------------------------------------


# Session-level overrides. A single hit anywhere in user text suppresses
# decisions-allowed regardless of any later assent — the user explicitly
# disclaimed intent to change code.
_NO_EDIT_INTENT = re.compile(
    r"\b(no\s+code\s+change(?:s)?|"
    r"no\s+changes\b|"
    r"just\s+(?:exploration|brainstorm\w*|thinking|discussing|checking|exploring)|"
    r"brainstorm\w*|"
    r"(?:just\s+)?exploring|"
    r"no\s+implementation)\b",
    re.IGNORECASE,
)


# Hedges that downgrade a same-sentence assent to weak confidence.
# We keep this conservative: "could" / "might" / "maybe" / "perhaps" are
# the canonical English hedges. "sounds (fine|ok|good)" is a backchannel
# acknowledgment, not a commitment.
_HEDGE = re.compile(
    r"\b(maybe|perhaps|might|"
    r"could(?:n'?t)?|"
    r"sounds\s+(?:fine|ok(?:ay)?|good|reasonable)|"
    r"not\s+sure|not\s+certain|unsure|"
    r"don'?t\s+know|i\s+(?:guess|think))\b",
    re.IGNORECASE,
)


# Strong assent verbs. "yes/ok/sure" are deliberately included even
# though they're noisy in conversation; the sentence-level hedge filter
# downgrades them when paired with hedging language, and the question
# filter drops them when ending in '?'. The ambiguous remainder is
# what step-7 (LLM judge) would resolve — for now we accept some noise
# in the strong-confidence bucket.
_ASSENT = re.compile(
    r"\b("
    r"let'?s\s+(?:go|do|use|ship|adopt|pick|take|try|land|build|implement)|"
    r"approve\w*|ratif\w+|sign[- ]?off|lock\s+it\s+in|"
    r"ship\s+it|merge\s+it|do\s+it|do\s+the\s+thing|"
    r"that'?s\s+(?:right|correct|the\s+one|it|ratified)|"
    r"\bdecided\b|\bdecision\b|"
    r"yes|yep|yeah|sure|ok(?:ay)?|fine|good|great|perfect"
    r")\b",
    re.IGNORECASE,
)


# Override patterns: explicit rejection coupled with a named alternative
# direction. These are stronger than plain assent because the user has
# considered (and dismissed) a model proposal.
_OVERRIDE = re.compile(
    r"\b("
    r"no\b[^.?!]{0,40}?\b(?:do|use|pick|go\s+with)\b|"
    r"instead\s+of\b|"
    r"not\s+\w+\s+(?:but|just|only)\b|"
    r"actually\s*[,—-]?\s*(?:let'?s|do|use|go)\b"
    r")",
    re.IGNORECASE,
)


# Explicit ADR cue. We're strict here: the user must literally invoke
# the ADR vocabulary. No "this is architectural enough to be an ADR" —
# that would re-introduce the same inference-from-context failure mode
# that made the original bad note misclassify discussion as decisions.
_ADR_CUE = re.compile(
    r"\b("
    r"adr\s+(?:this|it|that)|"
    r"record\s+(?:this|that)\s+as\s+an\s+adr|"
    r"file\s+an\s+adr|"
    r"adr-?worthy|"
    r"let'?s\s+(?:write|record|file)\s+an\s+adr"
    r")\b",
    re.IGNORECASE,
)


# Sentence splitter — naive but adequate for chat input. Splits on
# `[.!?]` followed by whitespace; keeps the trailing punctuation on
# the preceding sentence so the question detector can read it.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AssentHit:
    """One sentence-level signal that the user is committing or overriding.

    ``confidence`` is ``"weak"`` when the same sentence contains a hedge
    ("could", "might"), ``"strong"`` otherwise. The ``decisions_allowed``
    gate (see ``narrative_kind.NarrativeShape``) requires at least one
    strong hit OR ``files_modified`` to be non-empty.
    """

    turn_index: int
    kind: AssentKind
    confidence: Confidence
    excerpt: str  # truncated to ~200 chars


@dataclass(frozen=True)
class PrefilterSignals:
    """Aggregated regex signals over a slice of user turns."""

    no_edit_intent: bool = False
    assent_hits: tuple[AssentHit, ...] = field(default_factory=tuple)
    adr_flagged: bool = False

    @property
    def has_strong_assent(self) -> bool:
        return any(h.confidence == "strong" for h in self.assent_hits)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_signals(turns: list[Turn]) -> PrefilterSignals:
    """Scan user-turn text once. Returns the aggregated signals."""
    no_edit_intent = False
    adr_flagged = False
    hits: list[AssentHit] = []

    for i, t in enumerate(turns):
        if t.role != "user" or not t.text:
            continue
        text = t.text
        if not no_edit_intent and _NO_EDIT_INTENT.search(text):
            no_edit_intent = True
        if not adr_flagged and _ADR_CUE.search(text):
            adr_flagged = True
        hits.extend(_scan_user_turn(text, i))

    return PrefilterSignals(
        no_edit_intent=no_edit_intent,
        assent_hits=tuple(hits),
        adr_flagged=adr_flagged,
    )


def prior_assistant_text(turns: list[Turn], idx: int) -> str:
    """Return the most recent contiguous block of assistant prose before
    ``idx``, joined newline-separated.

    Skips tool_use, tool_result, and reasoning turns (which are interleaved
    with assistant text in modern Claude Code transcripts). Stops at the
    nearest user/system boundary. Returns ``""`` when only non-text
    assistant turns intervened (e.g. a sub-agent ``Task`` invocation
    whose result is the only thing between the prior user and the
    current one) — callers must treat that as "no prose context for the
    judge to consider", not as silent assent.

    This helper is best-effort: the regression net (step-9 fixture) covers
    the sub-agent edge case explicitly.
    """
    if idx <= 0 or idx > len(turns):
        return ""
    chunks: list[str] = []
    for j in range(idx - 1, -1, -1):
        t = turns[j]
        if t.role == "assistant":
            if t.text:
                chunks.append(t.text)
            # else: tool_use / reasoning / etc. — keep walking
            continue
        if t.role == "tool_result":
            continue
        # user / system / anything else → boundary
        break
    if not chunks:
        return ""
    return "\n".join(reversed(chunks))


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _scan_user_turn(text: str, turn_index: int) -> list[AssentHit]:
    """Per-sentence assent / override pass over one user turn."""
    out: list[AssentHit] = []
    for sentence in _split_sentences(text):
        if _is_question(sentence):
            continue
        confidence: Confidence = "weak" if _HEDGE.search(sentence) else "strong"
        if _OVERRIDE.search(sentence):
            # Override is intrinsically deliberate — confidence stays strong
            # even when same-sentence hedges are present (the user already
            # committed to dismissing the prior path).
            out.append(AssentHit(
                turn_index=turn_index,
                kind="override",
                confidence="strong",
                excerpt=_truncate(sentence),
            ))
            continue
        if _ASSENT.search(sentence):
            out.append(AssentHit(
                turn_index=turn_index,
                kind="assent",
                confidence=confidence,
                excerpt=_truncate(sentence),
            ))
    return out


def _split_sentences(text: str) -> list[str]:
    """Naive sentence split — sufficient for chat input. Empty fragments
    are dropped; trailing punctuation stays attached to the preceding
    sentence so :func:`_is_question` can read it."""
    parts = _SENTENCE_BOUNDARY.split(text.strip())
    return [p for p in parts if p]


def _is_question(sentence: str) -> bool:
    return sentence.rstrip().endswith("?")


def _truncate(sentence: str, limit: int = 200) -> str:
    s = sentence.strip()
    if len(s) <= limit:
        return s
    return s[: limit - 1] + "…"
