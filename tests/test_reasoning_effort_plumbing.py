"""Tests for Slice 2 of PRD #110 — reasoning_effort plumbing.

Covers:
- ``_OpenAIMessagesAPI.create`` forwards ``extra_body={"reasoning_effort": ...}``
  when the resolved tier opted in, and omits ``extra_body`` entirely when it
  didn't (byte-identical to the pre-PRD-#110 payload).
- ``LlmResponse.reasoning_effort`` carries the level applied — keeps the
  knowledge inside the client boundary (PRD's "Reasoning lives in the
  client + resolver, not the caller").
- ``synthesis.compose_session_note`` emits ``model_resolved`` + the
  applied ``reasoning_effort`` on the ``llm-response`` telemetry event.
- Regression bar: existing-style ``dict[str, str]`` callers (legacy tests)
  never see ``extra_body`` on the wire.

Slice-1 lifts the config + resolver. Slice-2 (this file) is the wire +
telemetry plumbing. Slice-3 already shipped the PHASE2 max-tokens bump.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Fake openai SDK — same minimal shape used by test_openai_backend.py
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
    def __init__(self, tool_calls: list[_FakeToolCall] | None = None,
                 content: str | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls or []
        self.role = "assistant"


class _FakeChoice:
    def __init__(self, message: _FakeMessage,
                 finish_reason: str = "tool_calls") -> None:
        self.message = message
        self.finish_reason = finish_reason


class _FakeUsage:
    prompt_tokens = 100
    completion_tokens = 20
    total_tokens = 120


class _FakeCompletion:
    def __init__(self, message: _FakeMessage, model: str = "echo-model") -> None:
        self.choices = [_FakeChoice(message)]
        self.model = model
        self.usage = _FakeUsage()


class _FakeChatCompletions:
    def __init__(self, response: _FakeCompletion) -> None:
        self._response = response
        self.last_kwargs: dict[str, Any] | None = None

    def create(self, **kwargs: Any) -> _FakeCompletion:
        self.last_kwargs = kwargs
        # Echo back the requested model id so callers can assert
        # ``resp.model`` carries the resolved id (matches real OpenAI
        # behaviour where the response payload echoes ``model``).
        if isinstance(kwargs.get("model"), str):
            self._response.model = kwargs["model"]
        return self._response


class _FakeChat:
    def __init__(self, completions: _FakeChatCompletions) -> None:
        self.completions = completions


class _FakeOpenAI:
    def __init__(self, **kwargs: Any) -> None:
        self.init_kwargs = kwargs
        default_payload = {
            "title": "Reasoning fixture",
            "summary": "stub",
        }
        msg = _FakeMessage(tool_calls=[_FakeToolCall(
            name="compose",
            arguments=json.dumps(default_payload),
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
# (a) Positive: configured reasoning_effort lands as extra_body kwarg
# ---------------------------------------------------------------------------


def test_resolved_model_with_reasoning_effort_forwards_extra_body(fake_openai):
    """High-tier ResolvedModel with reasoning_effort=high → kwargs has
    ``extra_body={"reasoning_effort": "high"}`` and model resolves to the id."""
    from lore_curator.llm_client import (
        OpenAICompatibleClient,
        ResolvedModel,
    )

    client = OpenAICompatibleClient(
        base_url="https://example.local/v1",
        api_key="sk-test",
        tier_to_model={
            "high": ResolvedModel(id="X", reasoning_effort="high"),
        },
    )

    client.messages.create(
        model="high",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{
            "name": "compose",
            "description": "",
            "input_schema": {"type": "object", "properties": {}},
        }],
        tool_choice={"type": "tool", "name": "compose"},
    )

    kwargs = client._client._completions.last_kwargs
    assert kwargs is not None
    assert kwargs["model"] == "X"
    assert kwargs["extra_body"] == {"reasoning_effort": "high"}


# ---------------------------------------------------------------------------
# (b) Negative: reasoning_effort=None means extra_body key absent
# ---------------------------------------------------------------------------


def test_resolved_model_without_reasoning_effort_omits_extra_body(fake_openai):
    """ResolvedModel(reasoning_effort=None) → ``extra_body`` is NOT in kwargs."""
    from lore_curator.llm_client import (
        OpenAICompatibleClient,
        ResolvedModel,
    )

    client = OpenAICompatibleClient(
        base_url="https://example.local/v1",
        api_key="sk-test",
        tier_to_model={
            "middle": ResolvedModel(id="Y", reasoning_effort=None),
        },
    )

    client.messages.create(
        model="middle",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{
            "name": "compose",
            "description": "",
            "input_schema": {"type": "object", "properties": {}},
        }],
        tool_choice={"type": "tool", "name": "compose"},
    )

    kwargs = client._client._completions.last_kwargs
    assert kwargs is not None
    assert kwargs["model"] == "Y"
    assert "extra_body" not in kwargs


# ---------------------------------------------------------------------------
# (c) LlmResponse carries the applied reasoning_effort
# ---------------------------------------------------------------------------


def test_llm_response_carries_reasoning_effort_high(fake_openai):
    """The response object exposes ``reasoning_effort="high"`` when forwarded."""
    from lore_curator.llm_client import (
        OpenAICompatibleClient,
        ResolvedModel,
    )

    client = OpenAICompatibleClient(
        base_url="https://example.local/v1",
        api_key="sk-test",
        tier_to_model={
            "high": ResolvedModel(id="X", reasoning_effort="high"),
        },
    )

    resp = client.messages.create(
        model="high",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{
            "name": "compose",
            "description": "",
            "input_schema": {"type": "object", "properties": {}},
        }],
        tool_choice={"type": "tool", "name": "compose"},
    )

    assert resp.reasoning_effort == "high"
    assert resp.model == "X"  # echoed back by the fake


def test_llm_response_reasoning_effort_none_when_unset(fake_openai):
    from lore_curator.llm_client import (
        OpenAICompatibleClient,
        ResolvedModel,
    )

    client = OpenAICompatibleClient(
        base_url="https://example.local/v1",
        api_key="sk-test",
        tier_to_model={
            "middle": ResolvedModel(id="Y", reasoning_effort=None),
        },
    )

    resp = client.messages.create(
        model="middle",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{
            "name": "compose",
            "description": "",
            "input_schema": {"type": "object", "properties": {}},
        }],
        tool_choice={"type": "tool", "name": "compose"},
    )

    assert resp.reasoning_effort is None


# ---------------------------------------------------------------------------
# (d) Literal pass-through (non-tier) — no extra_body even if other tiers
# have reasoning_effort configured. There's no ResolvedModel to consult.
# ---------------------------------------------------------------------------


def test_literal_model_passthrough_gets_no_extra_body(fake_openai):
    """``model="Mistral Small 4 119B"`` is a literal — no tier inference,
    no ResolvedModel lookup → no extra_body lands on the wire, even though
    other tiers have reasoning_effort configured."""
    from lore_curator.llm_client import (
        OpenAICompatibleClient,
        ResolvedModel,
    )

    client = OpenAICompatibleClient(
        base_url="https://example.local/v1",
        api_key="sk-test",
        tier_to_model={
            "high": ResolvedModel(id="X", reasoning_effort="high"),
        },
    )

    client.messages.create(
        model="Mistral Small 4 119B",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{
            "name": "compose",
            "description": "",
            "input_schema": {"type": "object", "properties": {}},
        }],
        tool_choice={"type": "tool", "name": "compose"},
    )

    kwargs = client._client._completions.last_kwargs
    assert kwargs is not None
    assert kwargs["model"] == "Mistral Small 4 119B"
    assert "extra_body" not in kwargs


# ---------------------------------------------------------------------------
# (e) Regression bar: legacy ``dict[str, str]`` callers (pre-ResolvedModel
# tests, external scripts) MUST NOT trip extra_body on the wire.
# ---------------------------------------------------------------------------


def test_legacy_string_tier_map_still_omits_extra_body(fake_openai):
    """``tier_to_model={"middle": "m-m"}`` — the legacy shape used by older
    tests and external scripts. Internally normalized to
    ResolvedModel(id="m-m", reasoning_effort=None), so no extra_body."""
    from lore_curator.llm_client import OpenAICompatibleClient

    client = OpenAICompatibleClient(
        base_url="https://example.local/v1",
        api_key="sk-test",
        tier_to_model={"simple": "m-s", "middle": "m-m", "high": "m-h"},
    )

    client.messages.create(
        model="middle",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{
            "name": "compose",
            "description": "",
            "input_schema": {"type": "object", "properties": {}},
        }],
        tool_choice={"type": "tool", "name": "compose"},
    )

    kwargs = client._client._completions.last_kwargs
    assert kwargs is not None
    assert kwargs["model"] == "m-m"
    assert "extra_body" not in kwargs


# ---------------------------------------------------------------------------
# (f) End-to-end: synthesis.compose_session_note → resolved request payload
# carries reasoning_effort AND the llm-response telemetry event surfaces it.
# ---------------------------------------------------------------------------


def test_compose_session_note_end_to_end_carries_reasoning_effort(fake_openai):
    """Drive the Phase 2 compose path with an OpenAI client whose ``high``
    tier opted into reasoning_effort=high. Assert:

    1. The recorded outgoing request kwargs carry
       ``extra_body={"reasoning_effort": "high"}`` and ``model="reasoning-X"``.
    2. The ``llm-response`` telemetry event carries the new
       ``model_resolved`` + ``reasoning_effort`` fields so future
       experiment writeups don't have to bypass the wrapper.
    """
    from lore_curator.llm_client import (
        OpenAICompatibleClient,
        ResolvedModel,
    )
    from lore_curator.synthesis import compose_session_note

    client = OpenAICompatibleClient(
        base_url="https://example.local/v1",
        api_key="sk-test",
        tier_to_model={
            "high": ResolvedModel(id="reasoning-X", reasoning_effort="high"),
        },
    )

    # Override the default fake response with one whose tool_call carries
    # fields that exist in the work-shape Phase 2 schema (``shape=None``
    # selects the work variant). Keys outside the schema get stripped by
    # the caller-side filter; ``title`` + ``summary_lede`` both survive.
    compose_payload = {
        "title": "Reasoning-mode narration",
        "description": "Stub description.",
        "summary_lede": "Stub Phase 2 compose payload.",
    }
    client._client._completions._response = _FakeCompletion(
        message=_FakeMessage(tool_calls=[_FakeToolCall(
            name="compose",
            arguments=json.dumps(compose_payload),
        )]),
        model="reasoning-X",
    )

    events: list[tuple[str, dict[str, Any]]] = []

    class _CaptureLogger:
        run_id = "test"

        def emit(self, name: str, **kw: Any) -> None:
            events.append((name, kw))

    out = compose_session_note(
        turns_text="some session text",
        activity_summary="",
        is_continuation=False,
        continues_wikilink=None,
        llm_client=client,
        model="high",  # tier name — Phase 2 resolves via tier_to_model
        logger=_CaptureLogger(),
        transcript_id="t-1",
        shape=None,
    )
    assert out is not None
    # ``title`` and ``summary_lede`` are real schema fields and survive
    # the caller-side schema-key filter.
    assert out["title"] == "Reasoning-mode narration"
    assert out["summary_lede"] == "Stub Phase 2 compose payload."

    # (1) On-the-wire payload carries reasoning_effort.
    kwargs = client._client._completions.last_kwargs
    assert kwargs is not None
    assert kwargs["model"] == "reasoning-X"
    assert kwargs["extra_body"] == {"reasoning_effort": "high"}

    # (2) Telemetry event surfaces resolved model + effort.
    resp_events = [(n, kw) for n, kw in events
                   if n == "llm-response" and kw.get("call") == "compose-session-note"]
    assert len(resp_events) == 1, events
    _, payload = resp_events[0]
    assert payload["model_resolved"] == "reasoning-X"
    assert payload["reasoning_effort"] == "high"


def test_compose_session_note_telemetry_when_reasoning_effort_unset(fake_openai):
    """Negative-case telemetry: an openai client without reasoning_effort
    configured still emits ``model_resolved`` (the literal model id) and
    ``reasoning_effort=None`` so the field is observable either way."""
    from lore_curator.llm_client import (
        OpenAICompatibleClient,
        ResolvedModel,
    )
    from lore_curator.synthesis import compose_session_note

    client = OpenAICompatibleClient(
        base_url="https://example.local/v1",
        api_key="sk-test",
        tier_to_model={
            "middle": ResolvedModel(id="plain-Y", reasoning_effort=None),
        },
    )

    compose_payload = {"title": "Plain", "summary_lede": "no reasoning."}
    client._client._completions._response = _FakeCompletion(
        message=_FakeMessage(tool_calls=[_FakeToolCall(
            name="compose",
            arguments=json.dumps(compose_payload),
        )]),
        model="plain-Y",
    )

    events: list[tuple[str, dict[str, Any]]] = []

    class _CaptureLogger:
        run_id = "test"

        def emit(self, name: str, **kw: Any) -> None:
            events.append((name, kw))

    out = compose_session_note(
        turns_text="some session text",
        activity_summary="",
        is_continuation=False,
        continues_wikilink=None,
        llm_client=client,
        model="middle",
        logger=_CaptureLogger(),
        transcript_id="t-2",
        shape=None,
    )
    assert out is not None

    kwargs = client._client._completions.last_kwargs
    assert kwargs is not None
    assert "extra_body" not in kwargs

    resp_events = [(n, kw) for n, kw in events
                   if n == "llm-response" and kw.get("call") == "compose-session-note"]
    assert len(resp_events) == 1
    payload = resp_events[0][1]
    assert payload["model_resolved"] == "plain-Y"
    assert payload["reasoning_effort"] is None
