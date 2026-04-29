"""Tests for the LLM-gated step-closure judgment.

The Stop hook calls into ``closure_judgment.judge_closure`` for every
commit × matching-step pair where a trailer didn't already short-circuit
the decision. The function returns a structured verdict (``done`` /
``in_progress`` / ``skip``) plus a confidence and a reason — no
side effects.

Tests use a fake LlmClient so the suite stays deterministic and offline.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from lore_curator.closure_judgment import (
    ClosureJudgment,
    LlmClientError,
    judge_closure,
)


# ---------------------------------------------------------------------------
# Fake LlmClient
# ---------------------------------------------------------------------------


@dataclass
class _FakeToolUseBlock:
    type: str = "tool_use"
    input: dict[str, Any] | None = None


@dataclass
class _FakeResponse:
    content: list[_FakeToolUseBlock]
    model: str = "fake-model"


class _FakeMessages:
    def __init__(self, response_input: dict[str, Any] | Exception) -> None:
        self._response_input = response_input
        self.last_call_kwargs: dict[str, Any] | None = None

    def create(self, **kwargs: Any) -> _FakeResponse:
        self.last_call_kwargs = kwargs
        if isinstance(self._response_input, Exception):
            raise self._response_input
        return _FakeResponse(content=[_FakeToolUseBlock(input=self._response_input)])


class _FakeClient:
    def __init__(self, response_input: dict[str, Any] | Exception) -> None:
        self.messages = _FakeMessages(response_input)
        self.backend_name = "fake"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_done_verdict_returned() -> None:
    client = _FakeClient(
        {"decision": "done", "confidence": 0.92, "reason": "implemented step-1"}
    )
    judgment = judge_closure(
        commit_sha="abc123",
        commit_msg="implement step-1: add foo",
        diff_summary="lib/foo.py | 42 ++++",
        plan_slug="test",
        step_id="step-1",
        step_title="Add foo",
        step_body="implement foo",
        current_status="pending",
        llm_client=client,
        model="fake-model",
    )
    assert judgment.decision == "done"
    assert judgment.confidence == pytest.approx(0.92)
    assert "implemented" in judgment.reason


def test_in_progress_verdict_returned() -> None:
    client = _FakeClient(
        {"decision": "in_progress", "confidence": 0.75, "reason": "wip — partial"}
    )
    judgment = judge_closure(
        commit_sha="def456",
        commit_msg="wip on step-2",
        diff_summary="lib/bar.py | 5 +",
        plan_slug="test",
        step_id="step-2",
        step_title="Add bar",
        step_body="implement bar",
        current_status="in_progress",
        llm_client=client,
        model="fake-model",
    )
    assert judgment.decision == "in_progress"


def test_skip_verdict_returned() -> None:
    client = _FakeClient(
        {"decision": "skip", "confidence": 0.3, "reason": "tangential edit"}
    )
    judgment = judge_closure(
        commit_sha="ghi789",
        commit_msg="rename helper",
        diff_summary="lib/foo.py | 1 +",
        plan_slug="test",
        step_id="step-1",
        step_title="x",
        step_body="y",
        current_status="pending",
        llm_client=client,
        model="fake-model",
    )
    assert judgment.decision == "skip"


# ---------------------------------------------------------------------------
# Tool-call shape: schema + invocation
# ---------------------------------------------------------------------------


def test_llm_invocation_uses_tool_choice_and_schema() -> None:
    client = _FakeClient(
        {"decision": "done", "confidence": 0.9, "reason": "ok"}
    )
    judge_closure(
        commit_sha="abc123",
        commit_msg="msg",
        diff_summary="stats",
        plan_slug="test",
        step_id="step-1",
        step_title="t",
        step_body="b",
        current_status="pending",
        llm_client=client,
        model="fake-model",
    )
    kwargs = client.messages.last_call_kwargs
    assert kwargs is not None
    # Forced tool use — only one valid output shape.
    assert kwargs["tool_choice"] == {
        "type": "tool",
        "name": "closure_judgment",
    }
    tools = kwargs["tools"]
    assert len(tools) == 1
    schema = tools[0]
    assert schema["name"] == "closure_judgment"
    assert schema["input_schema"]["properties"]["decision"]["enum"] == [
        "done",
        "in_progress",
        "skip",
    ]


def test_prompt_includes_commit_and_step_context() -> None:
    client = _FakeClient(
        {"decision": "done", "confidence": 0.9, "reason": "ok"}
    )
    judge_closure(
        commit_sha="abc123def",
        commit_msg="implement zorp",
        diff_summary="lib/zorp.py | 99 +",
        plan_slug="my-plan",
        step_id="step-1",
        step_title="Add zorp",
        step_body="implement the zorp module",
        current_status="pending",
        llm_client=client,
        model="fake-model",
    )
    prompt = client.messages.last_call_kwargs["messages"][0]["content"]
    # Sanity: every relevant context bit is in the prompt.
    assert "abc123def" in prompt
    assert "implement zorp" in prompt
    assert "lib/zorp.py" in prompt
    assert "my-plan" in prompt
    assert "step-1" in prompt
    assert "Add zorp" in prompt
    assert "implement the zorp module" in prompt
    assert "pending" in prompt


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


def test_llm_client_error_propagates() -> None:
    client = _FakeClient(LlmClientError("backend exploded"))
    with pytest.raises(LlmClientError):
        judge_closure(
            commit_sha="abc",
            commit_msg="m",
            diff_summary="d",
            plan_slug="p",
            step_id="step-1",
            step_title="t",
            step_body="b",
            current_status="pending",
            llm_client=client,
            model="fake-model",
        )


def test_no_tool_use_block_raises_value_error() -> None:
    # If the LLM returns a response without a tool_use block, that's a
    # contract violation — surface it loudly so the Stop hook can swallow
    # it as a "skip" rather than mask the underlying issue.
    @dataclass
    class _NoToolUseClient:
        backend_name: str = "fake"

        class _Msgs:
            def create(self, **_kwargs: Any) -> _FakeResponse:
                return _FakeResponse(content=[])

        messages: Any = None

        def __post_init__(self) -> None:
            self.messages = self._Msgs()

    client = _NoToolUseClient()
    with pytest.raises(ValueError, match="tool_use"):
        judge_closure(
            commit_sha="abc",
            commit_msg="m",
            diff_summary="d",
            plan_slug="p",
            step_id="step-1",
            step_title="t",
            step_body="b",
            current_status="pending",
            llm_client=client,
            model="fake-model",
        )


def test_malformed_tool_input_clamps_decision_to_skip() -> None:
    # If the LLM returns a decision string outside the enum, clamp to
    # "skip" with confidence 0 — defensive against schema drift.
    client = _FakeClient(
        {"decision": "maybe", "confidence": 0.5, "reason": "unclear"}
    )
    judgment = judge_closure(
        commit_sha="abc",
        commit_msg="m",
        diff_summary="d",
        plan_slug="p",
        step_id="step-1",
        step_title="t",
        step_body="b",
        current_status="pending",
        llm_client=client,
        model="fake-model",
    )
    assert judgment.decision == "skip"
    assert judgment.confidence == 0.0


def test_missing_confidence_defaults_to_zero() -> None:
    # Confidence is required by schema, but if absent, fall back to 0.
    client = _FakeClient({"decision": "done", "reason": "ok"})
    judgment = judge_closure(
        commit_sha="abc",
        commit_msg="m",
        diff_summary="d",
        plan_slug="p",
        step_id="step-1",
        step_title="t",
        step_body="b",
        current_status="pending",
        llm_client=client,
        model="fake-model",
    )
    assert judgment.decision == "done"
    assert judgment.confidence == 0.0
