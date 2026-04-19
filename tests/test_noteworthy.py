"""Tests for lore_curator.noteworthy — noteworthy filter."""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from lore_core.types import Turn, ToolCall, ToolResult
from lore_curator.noteworthy import classify_slice, NoteworthyResult


# ---------------------------------------------------------------------------
# Fake Anthropic client
# ---------------------------------------------------------------------------

class _FakeContentBlock:
    def __init__(self, type_, input_=None, text=None):
        self.type = type_
        self.input = input_
        self.text = text


class _FakeResponse:
    def __init__(self, content):
        self.content = content


class _FakeMessagesAPI:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


class _FakeAnthropicClient:
    def __init__(self, response):
        self.messages = _FakeMessagesAPI(response)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(data: dict) -> _FakeAnthropicClient:
    block = _FakeContentBlock(type_="tool_use", input_=data)
    return _FakeAnthropicClient(_FakeResponse([block]))


def _make_text_client() -> _FakeAnthropicClient:
    """Client that returns only a text block (no tool_use)."""
    block = _FakeContentBlock(type_="text", text="some text")
    return _FakeAnthropicClient(_FakeResponse([block]))


def _resolver(tier: str) -> str:
    return {"middle": "claude-sonnet-4-6", "simple": "claude-haiku-4-5"}[tier]


def _t(role: str = "user", **kwargs) -> Turn:
    """Convenience constructor: index=0, timestamp=None defaults."""
    return Turn(index=0, timestamp=None, role=role, **kwargs)


def _simple_turns() -> list[Turn]:
    return [_t(role="user", text="hello")]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_classify_returns_noteworthy_true_for_substantive_slice():
    data = {
        "noteworthy": True,
        "reason": "substantive refactor",
        "title": "Add ledger",
        "bullets": ["Added ledger module", "Tests passing"],
        "files_touched": ["ledger.py"],
        "entities": ["ledger"],
        "decisions": ["Use append-only log"],
    }
    client = _make_client(data)
    result = classify_slice(_simple_turns(), model_resolver=_resolver, anthropic_client=client)
    assert isinstance(result, NoteworthyResult)
    assert result.noteworthy is True
    assert result.reason == "substantive refactor"
    assert result.title == "Add ledger"


def test_classify_returns_noteworthy_false_for_trivial():
    data = {
        "noteworthy": False,
        "reason": "single tool question",
        "title": "Quick bash query",
        "bullets": [],
        "files_touched": [],
        "entities": [],
        "decisions": [],
    }
    client = _make_client(data)
    result = classify_slice(_simple_turns(), model_resolver=_resolver, anthropic_client=client)
    assert result.noteworthy is False
    assert result.reason == "single tool question"


def test_classify_truncates_long_tool_results_in_prompt():
    long_output = "\n".join(f"line {i}" for i in range(1000))
    tool_result = ToolResult(tool_call_id="t1", output=long_output)
    turns = [_t(role="tool", tool_result=tool_result)]
    data = {"noteworthy": False, "reason": "trivial", "title": "t"}
    client = _make_client(data)
    classify_slice(turns, model_resolver=_resolver, anthropic_client=client)

    sent = client.messages.calls[0]["messages"][0]["content"]
    assert "<1000 lines>" in sent
    # Full content must not be present
    assert "line 999" not in sent


def test_classify_drops_thinking_blocks_from_prompt():
    turns = [_t(role="assistant", reasoning="secret plan", text=None)]
    data = {"noteworthy": False, "reason": "trivial", "title": "t"}
    client = _make_client(data)
    classify_slice(turns, model_resolver=_resolver, anthropic_client=client)

    sent = client.messages.calls[0]["messages"][0]["content"]
    assert "secret plan" not in sent


def test_classify_uses_middle_tier_by_default():
    recorded = []

    def recording_resolver(tier: str) -> str:
        recorded.append(tier)
        return "claude-sonnet-4-6"

    client = _make_client({"noteworthy": True, "reason": "r", "title": "t"})
    classify_slice(_simple_turns(), model_resolver=recording_resolver, anthropic_client=client)
    assert recorded == ["middle"]


def test_classify_uses_simple_tier_when_configured(tmp_path):
    recorded = []

    def recording_resolver(tier: str) -> str:
        recorded.append(tier)
        return "claude-haiku-4-5"

    client = _make_client({"noteworthy": False, "reason": "r", "title": "t"})
    classify_slice(
        _simple_turns(),
        tier="simple",
        model_resolver=recording_resolver,
        anthropic_client=client,
        lore_root=tmp_path,
    )
    assert recorded == ["simple"]


def test_classify_raises_on_unknown_tier():
    client = _make_client({"noteworthy": False, "reason": "r", "title": "t"})
    with pytest.raises(ValueError, match="unknown tier"):
        classify_slice(
            _simple_turns(),
            tier="extreme",
            model_resolver=_resolver,
            anthropic_client=client,
        )


def test_simple_tier_writes_warning_once_per_lore_root(tmp_path):
    client1 = _make_client({"noteworthy": False, "reason": "r", "title": "t"})
    client2 = _make_client({"noteworthy": False, "reason": "r", "title": "t"})

    classify_slice(_simple_turns(), tier="simple", model_resolver=_resolver,
                   anthropic_client=client1, lore_root=tmp_path)
    classify_slice(_simple_turns(), tier="simple", model_resolver=_resolver,
                   anthropic_client=client2, lore_root=tmp_path)

    log_path = tmp_path / ".lore" / "warnings.log"
    assert log_path.exists()
    text = log_path.read_text()
    assert text.count("noteworthy-simple-tier-v1") == 1


def test_simple_tier_without_lore_root_is_silent():
    client = _make_client({"noteworthy": False, "reason": "r", "title": "t"})
    # Should not raise and not create any warning file
    result = classify_slice(
        _simple_turns(),
        tier="simple",
        model_resolver=_resolver,
        anthropic_client=client,
        lore_root=None,
    )
    assert result.noteworthy is False


def test_classify_returns_valueerror_on_missing_tool_use():
    client = _make_text_client()
    with pytest.raises(ValueError, match="no tool_use block"):
        classify_slice(_simple_turns(), model_resolver=_resolver, anthropic_client=client)


def test_classify_sends_correct_model_name():
    client = _make_client({"noteworthy": True, "reason": "r", "title": "t"})
    classify_slice(_simple_turns(), model_resolver=lambda _: "claude-sonnet-4-6",
                   anthropic_client=client)
    assert client.messages.calls[0]["model"] == "claude-sonnet-4-6"


def test_classify_forces_tool_choice():
    client = _make_client({"noteworthy": True, "reason": "r", "title": "t"})
    classify_slice(_simple_turns(), model_resolver=_resolver, anthropic_client=client)
    assert client.messages.calls[0]["tool_choice"] == {"type": "tool", "name": "classify"}


def test_classify_result_is_frozen_dataclass():
    client = _make_client({"noteworthy": True, "reason": "r", "title": "t"})
    result = classify_slice(_simple_turns(), model_resolver=_resolver, anthropic_client=client)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.noteworthy = False  # type: ignore[misc]
