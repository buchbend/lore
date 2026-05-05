"""Tests for the LLM-judged step_files inference module.

``infer_step_files`` is the LLM fallback path used by
``lore plan migrate-step-files --llm`` to backfill ``step_files``
frontmatter on plans authored before the ``Files:`` directive
convention. The function returns a structured ``StepFilesInference``
(per-step paths + per-step confidence + overall reason) — no side
effects.

Tests use a fake LlmClient so the suite stays deterministic and offline.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from lore_curator.step_files_inference import (
    LlmClientError,
    StepFilesInference,
    infer_step_files,
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


def test_per_step_files_returned() -> None:
    client = _FakeClient(
        {
            "step_files": [
                {"step_id": "step-1", "files": ["lib/foo.py"], "confidence": 0.9},
                {"step_id": "step-2", "files": ["lib/bar.py", "tests/test_bar.py"], "confidence": 0.8},
            ],
            "reasoning": "paths backticked in step bodies",
        }
    )
    inference = infer_step_files(
        plan_slug="test-plan",
        plan_title="Test plan",
        plan_body="### step-1\nUpdate `lib/foo.py`.\n\n### step-2\nAdd `lib/bar.py` + tests.",
        step_ids=["step-1", "step-2"],
        llm_client=client,
        model="fake-model",
    )
    assert inference.step_files == {
        "step-1": ["lib/foo.py"],
        "step-2": ["lib/bar.py", "tests/test_bar.py"],
    }
    assert inference.confidence == {"step-1": 0.9, "step-2": 0.8}
    assert "backticked" in inference.reason


def test_design_step_returns_empty_files() -> None:
    """High-confidence empty list — design/rollout step with no concrete files."""
    client = _FakeClient(
        {
            "step_files": [
                {"step_id": "step-1", "files": [], "confidence": 0.95},
            ],
            "reasoning": "step is pure design, no file edits",
        }
    )
    inference = infer_step_files(
        plan_slug="design",
        plan_title="Design",
        plan_body="### step-1\nDecide on the auth flow.",
        step_ids=["step-1"],
        llm_client=client,
        model="fake-model",
    )
    assert inference.step_files == {"step-1": []}
    assert inference.confidence["step-1"] == 0.95


# ---------------------------------------------------------------------------
# Tool-call shape
# ---------------------------------------------------------------------------


def test_tool_choice_forced() -> None:
    client = _FakeClient(
        {"step_files": [], "reasoning": "no steps"}
    )
    infer_step_files(
        plan_slug="x", plan_title="x", plan_body="body",
        step_ids=["step-1"], llm_client=client, model="m",
    )
    kwargs = client.messages.last_call_kwargs
    assert kwargs is not None
    assert kwargs["tool_choice"] == {"type": "tool", "name": "step_files"}
    assert len(kwargs["tools"]) == 1
    schema = kwargs["tools"][0]
    assert schema["name"] == "step_files"
    # additionalProperties guard at both levels
    assert schema["input_schema"]["additionalProperties"] is False
    items = schema["input_schema"]["properties"]["step_files"]["items"]
    assert items["additionalProperties"] is False


def test_prompt_includes_step_ids_and_body() -> None:
    client = _FakeClient(
        {"step_files": [], "reasoning": "ok"}
    )
    infer_step_files(
        plan_slug="my-plan", plan_title="My plan",
        plan_body="step body content here",
        step_ids=["step-1", "step-2", "step-3"],
        llm_client=client, model="m",
    )
    msg = client.messages.last_call_kwargs["messages"][0]["content"]
    assert "step-1, step-2, step-3" in msg
    assert "step body content here" in msg
    assert "my-plan" in msg
    assert "My plan" in msg


# ---------------------------------------------------------------------------
# Defensive parsing
# ---------------------------------------------------------------------------


def test_confidence_clamped_to_unit_interval() -> None:
    client = _FakeClient(
        {
            "step_files": [
                {"step_id": "step-1", "files": ["a.py"], "confidence": 1.5},
                {"step_id": "step-2", "files": ["b.py"], "confidence": -0.2},
            ],
            "reasoning": "out-of-range guard",
        }
    )
    inference = infer_step_files(
        plan_slug="x", plan_title="x", plan_body="b",
        step_ids=["step-1", "step-2"], llm_client=client, model="m",
    )
    assert inference.confidence["step-1"] == 1.0
    assert inference.confidence["step-2"] == 0.0


def test_non_string_files_dropped() -> None:
    client = _FakeClient(
        {
            "step_files": [
                {"step_id": "step-1", "files": ["lib/foo.py", "", None, 42], "confidence": 0.8},
            ],
            "reasoning": "junk filter",
        }
    )
    inference = infer_step_files(
        plan_slug="x", plan_title="x", plan_body="b",
        step_ids=["step-1"], llm_client=client, model="m",
    )
    assert inference.step_files["step-1"] == ["lib/foo.py"]


def test_invalid_entry_skipped() -> None:
    """An entry without step_id is dropped, not crashing the run."""
    client = _FakeClient(
        {
            "step_files": [
                {"step_id": "step-1", "files": ["a.py"], "confidence": 0.9},
                {"files": ["b.py"], "confidence": 0.5},  # missing step_id
                "not a dict",
            ],
            "reasoning": "garbage in",
        }
    )
    inference = infer_step_files(
        plan_slug="x", plan_title="x", plan_body="b",
        step_ids=["step-1"], llm_client=client, model="m",
    )
    assert inference.step_files == {"step-1": ["a.py"]}


def test_invalid_outer_shape_yields_empty() -> None:
    client = _FakeClient(
        {"step_files": "not-a-list", "reasoning": "wrong"}
    )
    inference = infer_step_files(
        plan_slug="x", plan_title="x", plan_body="b",
        step_ids=["step-1"], llm_client=client, model="m",
    )
    assert inference.step_files == {}
    assert inference.confidence == {}


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_llm_client_error_propagates() -> None:
    client = _FakeClient(LlmClientError("backend timed out"))
    with pytest.raises(LlmClientError, match="timed out"):
        infer_step_files(
            plan_slug="x", plan_title="x", plan_body="b",
            step_ids=["step-1"], llm_client=client, model="m",
        )


def test_missing_tool_use_raises_value_error() -> None:
    """Empty content list — no tool_use block to extract from."""

    class _EmptyResponse:
        content: list = []

    class _EmptyMessages:
        def create(self, **kwargs: Any) -> _EmptyResponse:
            return _EmptyResponse()

    class _EmptyClient:
        def __init__(self) -> None:
            self.messages = _EmptyMessages()

    with pytest.raises(ValueError, match="no tool_use"):
        infer_step_files(
            plan_slug="x", plan_title="x", plan_body="b",
            step_ids=["step-1"], llm_client=_EmptyClient(), model="m",
        )
