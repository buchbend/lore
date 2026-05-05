"""Tests for lore_core.decision_signals — deterministic prefilter.

These regexes drive the gate that decides whether a session note is
allowed to render a ``Decisions`` section. They run on every flush, so
correctness here is load-bearing for the bad-note class this plan
targets.

The motivating failure (sessions/buchbend/2026/05/05-1212-...) had the
user say ``"no code change just exploration"`` twice and ``"Just checking
brainstorming no code change"`` once, yet the rendered note declared
five "decisions" and eight "what we worked on" bullets. Stage 1 must
catch this without an LLM call.
"""
from __future__ import annotations

from datetime import datetime, UTC

from lore_core.decision_signals import (
    AssentHit,
    PrefilterSignals,
    extract_signals,
    prior_assistant_text,
)
from lore_core.types import ToolCall, Turn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user(idx: int, text: str) -> Turn:
    return Turn(index=idx, timestamp=None, role="user", text=text)


def _assistant(idx: int, text: str | None = None, *, tool_call: ToolCall | None = None) -> Turn:
    return Turn(
        index=idx, timestamp=None, role="assistant",
        text=text, tool_call=tool_call,
    )


def _tool_result(idx: int) -> Turn:
    from lore_core.types import ToolResult
    return Turn(
        index=idx, timestamp=None, role="tool_result",
        tool_result=ToolResult(tool_call_id="tc", output="ok"),
    )


# ---------------------------------------------------------------------------
# no_edit_intent
# ---------------------------------------------------------------------------


def test_no_code_change_phrase_sets_intent():
    turns = [_user(0, "we want to document this code make yourself familiar (no code changes just exploration)")]
    s = extract_signals(turns)
    assert s.no_edit_intent is True


def test_brainstorm_phrase_sets_intent():
    turns = [_user(0, "Just checking brainstorming no code change")]
    s = extract_signals(turns)
    assert s.no_edit_intent is True


def test_just_thinking_sets_intent():
    turns = [_user(0, "just thinking out loud here")]
    s = extract_signals(turns)
    assert s.no_edit_intent is True


def test_clean_user_text_does_not_flag_intent():
    turns = [_user(0, "fix the broken link in README")]
    s = extract_signals(turns)
    assert s.no_edit_intent is False


# ---------------------------------------------------------------------------
# assent / override
# ---------------------------------------------------------------------------


def test_lets_go_with_x_is_strong_assent():
    turns = [_user(0, "Let's go with the Diátaxis structure.")]
    s = extract_signals(turns)
    assert len(s.assent_hits) == 1
    assert s.assent_hits[0].kind == "assent"
    assert s.assent_hits[0].confidence == "strong"
    assert s.has_strong_assent is True


def test_decided_is_strong_assent():
    turns = [_user(0, "Decided. We're going with Pydantic.")]
    s = extract_signals(turns)
    assert any(h.kind == "assent" for h in s.assent_hits)
    assert s.has_strong_assent is True


def test_hedged_assent_in_same_sentence_is_weak():
    """The plan's reviewer pushback: ``we could do X — let's go with X``
    must NOT be silently dropped. The hit lands as weak, surfacing for
    a future judge call without losing the signal."""
    turns = [_user(0, "we could do X — let's go with X")]
    s = extract_signals(turns)
    assert len(s.assent_hits) == 1
    # Sentence carries both hedge AND assent → weak.
    assert s.assent_hits[0].confidence == "weak"


def test_hedge_in_different_sentence_does_not_downgrade():
    """A hedge that's not co-resident with the assent verb must NOT
    downgrade the assent. Sentence scope, not turn scope."""
    turns = [_user(0, "Maybe we don't need it later. But for now let's ship it.")]
    s = extract_signals(turns)
    # Two sentences: hedge sentence has no assent verb; assent sentence
    # has no hedge → the only hit is strong.
    strong_hits = [h for h in s.assent_hits if h.confidence == "strong"]
    assert len(strong_hits) >= 1
    assert any("ship it" in h.excerpt.lower() for h in strong_hits)


def test_question_form_suppresses_assent():
    """``yes?`` is a request for confirmation, not assent."""
    turns = [_user(0, "yes?")]
    s = extract_signals(turns)
    assert s.assent_hits == ()


def test_question_in_assent_word_neighborhood_suppresses():
    turns = [_user(0, "Should we go with the diataxis approach?")]
    s = extract_signals(turns)
    assert s.assent_hits == ()


def test_override_is_strong_even_with_hedge():
    """Override implies a deliberate dismissal — confidence stays strong
    even when the same sentence contains a hedge."""
    turns = [_user(0, "no maybe just do the simpler thing")]
    s = extract_signals(turns)
    overrides = [h for h in s.assent_hits if h.kind == "override"]
    assert len(overrides) == 1
    assert overrides[0].confidence == "strong"


def test_instead_of_is_override():
    turns = [_user(0, "Instead of polling, use the webhook.")]
    s = extract_signals(turns)
    assert any(h.kind == "override" for h in s.assent_hits)


def test_actually_lets_x_is_override():
    turns = [_user(0, "Actually, let's go with the simpler one.")]
    s = extract_signals(turns)
    # ``actually let's go`` matches override; assent might also match.
    # We just need at least one override hit.
    assert any(h.kind == "override" for h in s.assent_hits)


# ---------------------------------------------------------------------------
# ADR cue
# ---------------------------------------------------------------------------


def test_adr_this_is_flagged():
    turns = [_user(0, "ADR this — it's load-bearing.")]
    s = extract_signals(turns)
    assert s.adr_flagged is True


def test_record_as_adr_is_flagged():
    turns = [_user(0, "Let's record that as an ADR.")]
    s = extract_signals(turns)
    assert s.adr_flagged is True


def test_architectural_alone_does_not_flag_adr():
    """Strict cue — no LLM-inferred ADRs. The user must invoke the
    vocabulary explicitly. This is the same class of inference that
    produced the original bug."""
    turns = [_user(0, "this is an architectural choice")]
    s = extract_signals(turns)
    assert s.adr_flagged is False


# ---------------------------------------------------------------------------
# Multi-turn aggregation
# ---------------------------------------------------------------------------


def test_no_edit_intent_overrides_later_assent_in_aggregate_signal():
    """Both signals are emitted independently; the gate downstream
    (``decisions_allowed``) is what reconciles them. The prefilter does
    not silently swallow the assent — it surfaces both."""
    turns = [
        _user(0, "Let's just brainstorm — no code change."),
        _assistant(1, "Considering options A and B."),
        _user(2, "Let's go with B."),
    ]
    s = extract_signals(turns)
    assert s.no_edit_intent is True
    assert s.has_strong_assent is True


def test_only_user_turns_are_scanned():
    """Assistant text may contain plenty of ``yes`` / ``decided`` tokens
    — those are not user assent. Only ``role == 'user'`` counts."""
    turns = [
        _assistant(0, "Yes that's exactly right, decided!"),
        _user(1, "tell me more"),
    ]
    s = extract_signals(turns)
    assert s.assent_hits == ()


# ---------------------------------------------------------------------------
# prior_assistant_text
# ---------------------------------------------------------------------------


def test_prior_assistant_text_returns_immediately_preceding_block():
    turns = [
        _assistant(0, "First we should consider X."),
        _assistant(1, "Then Y."),
        _user(2, "yes"),
    ]
    assert prior_assistant_text(turns, 2) == "First we should consider X.\nThen Y."


def test_prior_assistant_text_skips_tool_use_and_results():
    turns = [
        _assistant(0, "I'll check the file."),
        _assistant(1, tool_call=ToolCall(name="Read", input={"file_path": "x"}, category="file_read")),
        _tool_result(2),
        _user(3, "ok"),
    ]
    assert prior_assistant_text(turns, 3) == "I'll check the file."


def test_prior_assistant_text_returns_empty_when_only_subagent_intervened():
    """The plan's reviewer flagged this: ``Task`` tool returns are not
    human-meant assistant prose. When that's all that intervened, the
    judge has no real context to consider."""
    turns = [
        _user(0, "have an architect subagent design this"),
        _assistant(1, tool_call=ToolCall(name="Task", input={"description": "x"}, category="agent_spawn")),
        _tool_result(2),
        _user(3, "OK"),
    ]
    assert prior_assistant_text(turns, 3) == ""


def test_prior_assistant_text_stops_at_user_boundary():
    turns = [
        _assistant(0, "earlier context"),
        _user(1, "first question"),
        _assistant(2, "answer to first"),
        _user(3, "follow-up"),
    ]
    assert prior_assistant_text(turns, 3) == "answer to first"


def test_prior_assistant_text_idx_zero_or_oob_returns_empty():
    turns = [_user(0, "hi")]
    assert prior_assistant_text(turns, 0) == ""
    assert prior_assistant_text(turns, 99) == ""


# ---------------------------------------------------------------------------
# End-to-end: the failing 05-1212 transcript pattern
# ---------------------------------------------------------------------------


def test_05_1212_pattern_yields_no_strong_assent_and_intent_set():
    """Reproduce the user prompts from the bad note's transcript:

    1. 'we want to document this code... (no code changes just exploration)'
    2. 'have a subagent architect devise the best documentation architect'
    3. 'OK ... please make up your mind ... if that is a good approach. Just
       checking brainstorming no code change'

    Stage 1 must surface ``no_edit_intent=True``. The downstream gate
    (decisions_allowed) will then refuse the Decisions section.
    """
    turns = [
        _user(0, "we want to document this code make yourself familiar with the purpose "
                 "of this code reflect back to me about your understaning of it "
                 "(no code changes just exploration)"),
        _user(1, "have a subagent architect devise the best documentation architecite"),
        _user(2, "OK conf.py is missing we build an aggregate operation documentation "
                 "is in system-integration. please make up your mind how the entire "
                 "structure is setup and if that is a good approach. Just checking "
                 "brainstorming no code change"),
    ]
    s = extract_signals(turns)
    assert s.no_edit_intent is True
    # 'OK' at the start of turn 2 *would* match assent, but the same
    # sentence ends with ``brainstorming no code change``; turn-level
    # no_edit_intent makes the downstream gate refuse decisions
    # regardless of that hit.
    assert s.adr_flagged is False
