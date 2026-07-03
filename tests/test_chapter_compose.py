"""One-call chapter composer — stub-LLM replay contract tests.

Every LLM interaction is faked. These tests assert the *call contract*
(one call per attempt, slice sent once, note-so-far included, retry
carries gate feedback, anchor lint, gate-driven withhold) and NEVER the
quality of composed prose — no LLM-as-judge appears here.

The stub records each ``messages.create`` kwargs and returns a queued
tool_use payload, mirroring the saved-buffer replay harness used for the
prompt experiments.
"""

from __future__ import annotations

from typing import Any

import pytest
from lore_core.note_document import Chapter, TopicBlock
from lore_curator.chapter_compose import (
    CHAPTER_MAX_ATTEMPTS,
    ComposeStatus,
    Gate,
    GateResult,
    PassThroughGate,
    chapter_anchor_lint,
    chapter_tool_schema,
    compose_chapter,
    render_chapter_body,
)

# ---------------------------------------------------------------------------
# Stub LLM (records call kwargs, replays queued tool_use payloads)
# ---------------------------------------------------------------------------


class _FakeBlock:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.type = "tool_use"
        self.input = payload


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], model: str = "test-model") -> None:
        self.content = [_FakeBlock(payload)]
        self.model = model


class _RecordingMessages:
    """Queue of payloads; ``None`` in the queue simulates a raising call."""

    def __init__(self, payloads: list[dict[str, Any] | None]) -> None:
        self._payloads = list(payloads)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        payload = self._payloads.pop(0) if self._payloads else {}
        if payload is None:
            raise RuntimeError("simulated LLM failure")
        return _FakeResponse(payload)


class _FakeClient:
    def __init__(self, messages: _RecordingMessages) -> None:
        self.messages = messages


def _prompt_text(call: dict[str, Any]) -> str:
    return call["messages"][0]["content"]


def _valid_payload() -> dict[str, Any]:
    return {
        "blocks": [
            {
                "lead": "The composer sent the slice once.",
                "body": "It packed the buffered turns into a single call.",
                "anchor": 2,
            },
            {
                "lead": "The retry carried the gate feedback.",
                "body": "A second attempt reused the withheld verdict.",
                "anchor": 4,
            },
        ]
    }


# ---------------------------------------------------------------------------
# Gate seam (the interface the publish gate implements)
# ---------------------------------------------------------------------------


def test_gate_result_ok_and_withheld_constructors():
    ok = GateResult.ok()
    assert ok.passed is True
    assert ok.category == ""
    assert ok.feedback == ""
    withheld = GateResult.withheld("secret", "contains an API key")
    assert withheld.passed is False
    assert withheld.category == "secret"
    assert withheld.feedback == "contains an API key"


def test_passthrough_gate_always_passes():
    gate = PassThroughGate()
    assert isinstance(gate, Gate)
    assert gate.evaluate("anything at all @1").passed is True


# ---------------------------------------------------------------------------
# Anchor lint (deterministic, no LLM)
# ---------------------------------------------------------------------------


def test_anchor_lint_accepts_in_slice_anchors():
    chapter = Chapter(
        blocks=[
            TopicBlock(lead="a", body="", anchor_turn=3),
            TopicBlock(lead="b", body="", anchor_turn=7),
        ]
    )
    assert chapter_anchor_lint(chapter, from_turn=2, to_turn=8) == []


def test_anchor_lint_rejects_anchor_above_slice():
    chapter = Chapter(
        blocks=[
            TopicBlock(lead="a", body="", anchor_turn=3),
            TopicBlock(lead="b", body="", anchor_turn=99),
        ]
    )
    offenders = chapter_anchor_lint(chapter, from_turn=2, to_turn=8)
    assert offenders == [(1, 99)]


def test_anchor_lint_rejects_anchor_below_slice_and_missing():
    chapter = Chapter(
        blocks=[
            TopicBlock(lead="a", body="", anchor_turn=1),  # below from_turn
            TopicBlock(lead="b", body="", anchor_turn=-1),  # missing anchor
        ]
    )
    offenders = chapter_anchor_lint(chapter, from_turn=2, to_turn=8)
    assert offenders == [(0, 1), (1, -1)]


# ---------------------------------------------------------------------------
# One call per attempt; slice sent once; note-so-far included
# ---------------------------------------------------------------------------


def test_single_call_composes_and_sends_slice_once():
    msgs = _RecordingMessages([_valid_payload()])
    client = _FakeClient(msgs)

    result = compose_chapter(
        slice_text="[user@2] hi\n[assistant@4] yo",
        slice_from_turn=2,
        slice_to_turn=4,
        note_so_far="> disclaimer line\n\nprior chapter body",
        llm_client=client,
        model="test-model",
    )

    assert result.status is ComposeStatus.COMPOSED
    assert result.attempts == 1
    assert result.chapter is not None
    assert [b.anchor_turn for b in result.chapter.blocks] == [2, 4]

    # Exactly one LLM call; correct tool routed; slice text present once.
    assert len(msgs.calls) == 1
    call = msgs.calls[0]
    assert call["model"] == "test-model"
    assert call["tool_choice"] == {"type": "tool", "name": "compose_chapter"}
    prompt = _prompt_text(call)
    assert prompt.count("[user@2] hi\n[assistant@4] yo") == 1


def test_note_so_far_included_in_the_call():
    msgs = _RecordingMessages([_valid_payload()])
    client = _FakeClient(msgs)
    note_so_far = "> **Lab-notebook session note.**\n\nPrior chapter about the buffer store."

    compose_chapter(
        slice_text="[user@0] hi",
        slice_from_turn=0,
        slice_to_turn=0,
        note_so_far=note_so_far,
        llm_client=client,
        model="m",
    )
    prompt = _prompt_text(msgs.calls[0])
    assert "Prior chapter about the buffer store." in prompt


def test_prompt_carries_phrasing_rules():
    msgs = _RecordingMessages([_valid_payload()])
    client = _FakeClient(msgs)
    compose_chapter(
        slice_text="[user@0] hi",
        slice_from_turn=0,
        slice_to_turn=0,
        note_so_far="note",
        llm_client=client,
        model="m",
    )
    prompt = _prompt_text(msgs.calls[0]).lower()
    assert "past tense" in prompt
    assert "continued:" in prompt
    # Self-sufficient lead: no pronouns reaching into the body.
    assert "self-sufficient" in prompt


# ---------------------------------------------------------------------------
# Continuation blocks reference earlier topics
# ---------------------------------------------------------------------------


def test_continuation_block_round_trips():
    payload = {
        "blocks": [
            {
                "lead": "",
                "body": "The gate loop was reworked to carry feedback.",
                "anchor": 3,
                "continued": True,
                "continued_topic": "The retry loop",
            },
        ]
    }
    msgs = _RecordingMessages([payload])
    client = _FakeClient(msgs)

    result = compose_chapter(
        slice_text="[user@3] resume the retry loop",
        slice_from_turn=3,
        slice_to_turn=3,
        note_so_far="earlier: The retry loop",
        llm_client=client,
        model="m",
    )
    assert result.status is ComposeStatus.COMPOSED
    block = result.chapter.blocks[0]
    assert block.continued is True
    assert block.continued_topic == "The retry loop"


# ---------------------------------------------------------------------------
# Anchor lint drives a retry; second attempt carries corrective feedback
# ---------------------------------------------------------------------------


def test_out_of_slice_anchor_triggers_retry_then_composes():
    bad = {"blocks": [{"lead": "a", "body": "b", "anchor": 999}]}
    good = _valid_payload()
    msgs = _RecordingMessages([bad, good])
    client = _FakeClient(msgs)

    result = compose_chapter(
        slice_text="[user@2] x\n[assistant@4] y",
        slice_from_turn=2,
        slice_to_turn=4,
        note_so_far="note",
        llm_client=client,
        model="m",
    )
    assert result.status is ComposeStatus.COMPOSED
    assert result.attempts == 2
    assert len(msgs.calls) == 2
    # The retry prompt names the offending anchor range.
    retry_prompt = _prompt_text(msgs.calls[1])
    assert "2" in retry_prompt and "4" in retry_prompt
    assert "anchor" in retry_prompt.lower()


def test_persistent_out_of_slice_anchor_fails():
    bad = {"blocks": [{"lead": "a", "body": "b", "anchor": 999}]}
    msgs = _RecordingMessages([bad, dict(bad)])
    client = _FakeClient(msgs)
    result = compose_chapter(
        slice_text="[user@2] x",
        slice_from_turn=2,
        slice_to_turn=4,
        note_so_far="note",
        llm_client=client,
        model="m",
    )
    assert result.status is ComposeStatus.FAILED
    assert result.attempts == CHAPTER_MAX_ATTEMPTS


# ---------------------------------------------------------------------------
# Gate seam — withhold drives the retry with feedback; two withholds defer
# ---------------------------------------------------------------------------


class _WithholdOnceGate:
    """WITHHELD on the first evaluate, PASS thereafter."""

    def __init__(self, category: str, feedback: str) -> None:
        self._category = category
        self._feedback = feedback
        self._seen = 0
        self.texts: list[str] = []

    def evaluate(self, chapter_text: str) -> GateResult:
        self.texts.append(chapter_text)
        self._seen += 1
        if self._seen == 1:
            return GateResult.withheld(self._category, self._feedback)
        return GateResult.ok()


class _AlwaysWithholdGate:
    def __init__(self, category: str, feedback: str) -> None:
        self._category = category
        self._feedback = feedback
        self.texts: list[str] = []

    def evaluate(self, chapter_text: str) -> GateResult:
        self.texts.append(chapter_text)
        return GateResult.withheld(self._category, self._feedback)


def test_gate_withhold_retries_with_feedback_then_passes():
    gate = _WithholdOnceGate("phrasing", "remove the imperative lead")
    msgs = _RecordingMessages([_valid_payload(), _valid_payload()])
    client = _FakeClient(msgs)

    result = compose_chapter(
        slice_text="[user@2] x\n[assistant@4] y",
        slice_from_turn=2,
        slice_to_turn=4,
        note_so_far="note",
        llm_client=client,
        model="m",
        gate=gate,
    )
    assert result.status is ComposeStatus.COMPOSED
    assert result.attempts == 2
    # The gate saw the rendered chapter text (leads/bodies/anchors).
    assert "The composer sent the slice once." in gate.texts[0]
    # The retry prompt carried the gate's feedback verbatim.
    retry_prompt = _prompt_text(msgs.calls[1])
    assert "remove the imperative lead" in retry_prompt


def test_two_withholds_returns_withheld_outcome():
    gate = _AlwaysWithholdGate("secret", "high-entropy token present")
    msgs = _RecordingMessages([_valid_payload(), _valid_payload()])
    client = _FakeClient(msgs)

    result = compose_chapter(
        slice_text="[user@2] x\n[assistant@4] y",
        slice_from_turn=2,
        slice_to_turn=4,
        note_so_far="note",
        llm_client=client,
        model="m",
        gate=gate,
    )
    assert result.status is ComposeStatus.WITHHELD
    assert result.attempts == CHAPTER_MAX_ATTEMPTS
    assert result.withheld_category == "secret"
    assert result.withheld_feedback == "high-entropy token present"
    # The withheld composed text is surfaced for quarantine downstream.
    assert "The composer sent the slice once." in result.withheld_text
    assert result.chapter is None


def test_default_gate_is_passthrough_standalone():
    # No gate injected — the composer runs the replay standalone.
    msgs = _RecordingMessages([_valid_payload()])
    client = _FakeClient(msgs)
    result = compose_chapter(
        slice_text="[user@2] x\n[assistant@4] y",
        slice_from_turn=2,
        slice_to_turn=4,
        note_so_far="note",
        llm_client=client,
        model="m",
    )
    assert result.status is ComposeStatus.COMPOSED


# ---------------------------------------------------------------------------
# LLM failure path
# ---------------------------------------------------------------------------


def test_llm_failure_both_attempts_returns_failed():
    msgs = _RecordingMessages([None, None])
    client = _FakeClient(msgs)
    result = compose_chapter(
        slice_text="[user@0] x",
        slice_from_turn=0,
        slice_to_turn=0,
        note_so_far="note",
        llm_client=client,
        model="m",
    )
    assert result.status is ComposeStatus.FAILED
    assert result.attempts == CHAPTER_MAX_ATTEMPTS
    assert len(msgs.calls) == CHAPTER_MAX_ATTEMPTS


def test_empty_blocks_returns_failed():
    msgs = _RecordingMessages([{"blocks": []}, {"blocks": []}])
    client = _FakeClient(msgs)
    result = compose_chapter(
        slice_text="[user@0] x",
        slice_from_turn=0,
        slice_to_turn=0,
        note_so_far="note",
        llm_client=client,
        model="m",
    )
    assert result.status is ComposeStatus.FAILED


# ---------------------------------------------------------------------------
# Tool schema shape
# ---------------------------------------------------------------------------


def test_chapter_tool_schema_shape():
    schema = chapter_tool_schema()
    assert schema["name"] == "compose_chapter"
    props = schema["input_schema"]["properties"]
    assert "blocks" in props
    block_props = props["blocks"]["items"]["properties"]
    assert set(block_props) >= {"lead", "body", "anchor", "continued", "continued_topic"}
    assert schema["input_schema"]["required"] == ["blocks"]


def test_render_chapter_body_includes_lead_body_anchor():
    chapter = Chapter(blocks=[TopicBlock(lead="Bold lead.", body="Prose body.", anchor_turn=5)])
    text = render_chapter_body(chapter)
    assert "**Bold lead.**" in text
    assert "Prose body." in text
    assert "@5" in text


# ---------------------------------------------------------------------------
# Deletion: two-region renderer / regions module gone; gather still imports
# ---------------------------------------------------------------------------


def test_regions_module_deleted():
    with pytest.raises(ImportError):
        __import__("lore_core.regions")


def test_briefing_gather_still_imports():
    from lore_core.briefing.gather import gather  # noqa: F401


def test_synthesis_no_longer_imports_render_regions():
    import lore_curator.synthesis as synthesis

    assert not hasattr(synthesis, "render_regions")
