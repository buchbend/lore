"""Acceptance tests for the LlmClient protocol and response types (Task 1)."""
from __future__ import annotations

from typing import Any

from lore_curator.llm_client import LlmClient, LlmClientError, LlmResponse, ToolUseBlock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeMessages:
    def create(self, **kwargs: Any) -> Any:
        return None


class _FakeClient:
    messages = _FakeMessages()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_llm_client_protocol_accepts_anthropic_shape() -> None:
    """An object with .messages.create(**kw) satisfies the LlmClient protocol."""
    client = _FakeClient()
    assert isinstance(client, LlmClient)


def test_tool_use_block_matches_anthropic_contract() -> None:
    """ToolUseBlock(input=...).type == 'tool_use' and .input is preserved."""
    block = ToolUseBlock(input={"a": 1})
    assert block.type == "tool_use"
    assert block.input == {"a": 1}


def test_llm_response_round_trip() -> None:
    """LlmResponse containing a ToolUseBlock is walkable by the existing extractor pattern."""
    block = ToolUseBlock(input={"label": "foo", "score": 0.9})
    resp = LlmResponse(content=[block])

    # Replicate the exact extractor pattern used in noteworthy.py, cluster.py, etc.
    found_input = None
    for b in getattr(resp, "content", []):
        btype = getattr(b, "type", None) or (b.get("type") if isinstance(b, dict) else None)
        if btype == "tool_use":
            inp = getattr(b, "input", None)
            if inp is None and isinstance(b, dict):
                inp = b.get("input")
            if isinstance(inp, dict):
                found_input = inp
                break

    assert found_input == {"label": "foo", "score": 0.9}


def test_llm_client_error_is_runtimeerror() -> None:
    """LlmClientError is a subclass of RuntimeError."""
    assert issubclass(LlmClientError, RuntimeError)
