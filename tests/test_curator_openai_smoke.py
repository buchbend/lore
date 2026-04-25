"""End-to-end smoke test: curator code paths work with the OpenAI backend.

The grumpy-dev review of v0.9.0 (`docs/REVIEW-2026-04-25-three-lens-…`)
flagged that the LlmClient abstraction had been introduced but the
curators still took an ``anthropic_client`` parameter and never went
through the factory — meaning the OpenAI backend was de-facto unreachable
from the actual call sites. The Phase 0 cleanup renamed the parameter
to ``llm_client`` everywhere; this test proves that the OpenAI client
returned by ``make_llm_client(backend="openai")`` is actually accepted
by a real curator function and produces the expected result.

We don't hit a real OpenAI endpoint — the existing
``tests/test_openai_backend.py`` covers the protocol-level translation.
What this test adds is the *curator → LlmClient → OpenAICompatibleClient*
hand-off, which the rename was meant to unblock.
"""
from __future__ import annotations

import json
import sys
import types

import pytest

from lore_core.types import Turn
from lore_curator.noteworthy import NoteworthyResult, classify_slice


# ---------------------------------------------------------------------------
# Fake openai SDK — copy of the minimal shape used in test_openai_backend
# ---------------------------------------------------------------------------


class _FakeFn:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, name: str, arguments: str) -> None:
        self.id = "call_1"
        self.type = "function"
        self.function = _FakeFn(name, arguments)


class _FakeMessage:
    def __init__(self, tool_calls: list[_FakeToolCall]) -> None:
        self.content = None
        self.tool_calls = tool_calls
        self.role = "assistant"


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message
        self.finish_reason = "tool_calls"


class _FakeUsage:
    prompt_tokens = 100
    completion_tokens = 20
    total_tokens = 120


class _FakeCompletion:
    def __init__(self, message: _FakeMessage, model: str = "gpt-test") -> None:
        self.choices = [_FakeChoice(message)]
        self.model = model
        self.usage = _FakeUsage()


class _FakeChatCompletions:
    def __init__(self, response: _FakeCompletion) -> None:
        self._response = response
        self.last_kwargs: dict | None = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return self._response


class _FakeChat:
    def __init__(self, completions: _FakeChatCompletions) -> None:
        self.completions = completions


class _FakeOpenAI:
    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        # Default: a noteworthy=True classify tool_call
        classify_payload = {
            "noteworthy": True,
            "reason": "substantive refactor",
            "title": "Phase 0 cleanup landed",
            "summary": (
                "End-to-end smoke verifies the OpenAI backend is reachable "
                "through the renamed llm_client parameter."
            ),
            "bullets": ["rename done", "tests pass"],
            "files_touched": ["lib/lore_curator/noteworthy.py"],
            "entities": ["llm_client"],
            "decisions": [],
        }
        msg = _FakeMessage(tool_calls=[_FakeToolCall(
            name="classify",
            arguments=json.dumps(classify_payload),
        )])
        self._completions = _FakeChatCompletions(_FakeCompletion(msg))
        self.chat = _FakeChat(self._completions)


@pytest.fixture()
def fake_openai(monkeypatch: pytest.MonkeyPatch):
    fake_mod = types.ModuleType("openai")
    fake_mod.OpenAI = _FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake_mod)
    return fake_mod


# ---------------------------------------------------------------------------
# Smoke test: factory → curator
# ---------------------------------------------------------------------------


def test_classify_slice_works_end_to_end_with_openai_backend(
    fake_openai, monkeypatch
):
    """make_llm_client(backend='openai') feeds classify_slice without crashing.

    Proves the v0.9.0 rename closes the gap the grumpy review flagged:
    a real curator entrypoint now accepts the OpenAI-compatible client
    and walks the response correctly.
    """
    monkeypatch.setenv("LORE_OPENAI_BASE_URL", "https://example.local/v1")
    monkeypatch.setenv("LORE_OPENAI_API_KEY", "sk-smoke-test")
    monkeypatch.setenv("LORE_OPENAI_MODEL_MIDDLE", "test-middle")
    # Force-skip the cascade so the LLM path actually runs (otherwise a
    # one-turn "hello" slice would short-circuit as trivial).
    monkeypatch.setenv("LORE_NOTEWORTHY_MODE", "llm_only")

    from lore_curator.llm_client import OpenAICompatibleClient, make_llm_client

    client = make_llm_client(backend="openai")
    assert isinstance(client, OpenAICompatibleClient)

    turns = [Turn(
        index=0,
        timestamp=None,
        role="user",
        text="Refactor the curator to drop the redundant Protocol class.",
    )]
    result = classify_slice(
        turns,
        model_resolver=lambda tier: {
            "middle": "claude-sonnet-4-6", "simple": "claude-haiku-4-5"
        }[tier],
        llm_client=client,
    )

    assert isinstance(result, NoteworthyResult)
    assert result.noteworthy is True
    assert result.title == "Phase 0 cleanup landed"
    # The OpenAI fake recorded the translated request — the curator's
    # tool_choice={"type":"tool","name":"classify"} should have become
    # tool_choice="required" (the widest-compatible OSS-gateway form).
    assert client._client._completions.last_kwargs["tool_choice"] == "required"
