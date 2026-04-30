"""Tests for lore_curator.summary_merge — LLM-driven summary merge."""
from __future__ import annotations

from typing import Any

from lore_curator.llm_client import LlmClientError
from lore_curator.summary_merge import merge_descriptions


class _Block:
    def __init__(self, type_: str, input_: dict | None = None) -> None:
        self.type = type_
        self.input = input_ or {}


class _Response:
    def __init__(self, content: list[_Block]) -> None:
        self.content = content


class _MessagesAPI:
    def __init__(self, response: _Response | Exception) -> None:
        self._response = response
        self.calls: list[dict] = []

    def create(self, **kwargs: Any):
        self.calls.append(kwargs)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class _Client:
    def __init__(self, response: _Response | Exception) -> None:
        self.messages = _MessagesAPI(response)


def _client_returning(merged: str) -> _Client:
    return _Client(_Response([_Block("tool_use", {"merged": merged})]))


def test_merge_short_circuits_when_new_empty():
    """No new info → existing wins, no LLM call."""
    client = _client_returning("never-called")
    out = merge_descriptions(
        existing="anchor", new="",
        llm_client=client, model="m",
    )
    assert out == "anchor"
    assert client.messages.calls == []


def test_merge_short_circuits_when_existing_empty():
    """Nothing to anchor against → new is used as-is, no LLM call."""
    client = _client_returning("never-called")
    out = merge_descriptions(
        existing="", new="fresh",
        llm_client=client, model="m",
    )
    assert out == "fresh"
    assert client.messages.calls == []


def test_merge_short_circuits_when_strings_equal():
    """Identical existing + new → no merge needed."""
    client = _client_returning("never-called")
    out = merge_descriptions(
        existing="same",
        new="same",
        llm_client=client, model="m",
    )
    assert out == "same"
    assert client.messages.calls == []


def test_merge_short_circuits_on_whitespace_only_diff():
    """Trailing/leading whitespace differences are not real differences."""
    client = _client_returning("never-called")
    out = merge_descriptions(
        existing="  same  ",
        new="same",
        llm_client=client, model="m",
    )
    assert out == "  same  "
    assert client.messages.calls == []


def test_merge_calls_llm_when_both_substantive_and_distinct():
    """Both sides non-empty + different → LLM merge."""
    client = _client_returning("merged result")
    out = merge_descriptions(
        existing="morning framing",
        new="afternoon framing",
        new_bullets=["did X", "did Y"],
        new_decisions=["chose A"],
        llm_client=client,
        model="claude-sonnet-4-6",
    )
    assert out == "merged result"
    assert len(client.messages.calls) == 1
    sent = client.messages.calls[0]
    assert sent["model"] == "claude-sonnet-4-6"
    assert sent["tool_choice"] == {"type": "tool", "name": "merge_summary"}
    prompt = sent["messages"][0]["content"]
    # Both anchor + new context end up in the prompt.
    assert "morning framing" in prompt
    assert "afternoon framing" in prompt
    # Bullets + decisions are surfaced as additional context.
    assert "did X" in prompt
    assert "chose A" in prompt


def test_merge_strips_returned_whitespace():
    """LLM returns "  text\\n" → caller gets "text"."""
    client = _client_returning("  merged result\n")
    out = merge_descriptions(
        existing="a", new="b",
        llm_client=client, model="m",
    )
    assert out == "merged result"


def test_merge_falls_back_to_existing_on_llm_error():
    """LlmClientError → preserve existing rather than blanking it.

    Additive contract: a 5xx / timeout / oversize-prompt failure on the
    merge call must never erase the user's note framing."""
    client = _Client(LlmClientError("upstream 503"))
    out = merge_descriptions(
        existing="anchor", new="b",
        llm_client=client, model="m",
    )
    assert out == "anchor"


def test_merge_falls_back_on_unexpected_exception():
    """Any other exception type also degrades to existing."""
    client = _Client(RuntimeError("boom"))
    out = merge_descriptions(
        existing="anchor", new="b",
        llm_client=client, model="m",
    )
    assert out == "anchor"


def test_merge_falls_back_on_empty_tool_use():
    """Malformed response (no tool_use block) → existing wins."""
    client = _Client(_Response([_Block("text")]))
    out = merge_descriptions(
        existing="anchor", new="b",
        llm_client=client, model="m",
    )
    assert out == "anchor"


def test_merge_falls_back_on_missing_merged_field():
    """tool_use block but no ``merged`` key → existing wins."""
    client = _Client(_Response([_Block("tool_use", {"other": "x"})]))
    out = merge_descriptions(
        existing="anchor", new="b",
        llm_client=client, model="m",
    )
    assert out == "anchor"


def test_merge_falls_back_on_empty_merged_string():
    """``merged`` is the empty string → existing wins (defensive)."""
    client = _client_returning("")
    out = merge_descriptions(
        existing="anchor", new="b",
        llm_client=client, model="m",
    )
    assert out == "anchor"


def test_merge_caps_bullets_in_prompt():
    """Bullets are capped to 8 to keep the merge prompt bounded; extras
    don't produce 100-line prompts."""
    client = _client_returning("ok")
    bullets = [f"bullet{i}" for i in range(20)]
    merge_descriptions(
        existing="a", new="b",
        new_bullets=bullets,
        llm_client=client, model="m",
    )
    prompt = client.messages.calls[0]["messages"][0]["content"]
    assert "bullet0" in prompt
    assert "bullet7" in prompt
    assert "bullet8" not in prompt
