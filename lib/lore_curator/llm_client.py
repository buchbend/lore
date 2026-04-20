from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class LlmClientError(RuntimeError):
    """Raised by LlmClient implementations on unrecoverable failure.

    Concrete reasons (exit-nonzero, timeout, malformed JSON, missing
    `structured_output`, binary-not-found) go in the exception message.
    Callers treat this the same way they treated an anthropic SDK error:
    log it and skip the slice (existing call sites already catch Exception).
    """


@dataclass(frozen=True)
class ToolUseBlock:
    """Mimics an anthropic.types.ToolUseBlock for the subset curators use.

    Existing code walks `resp.content`, checks `block.type == "tool_use"`
    and reads `block.input`. This dataclass satisfies that shape.
    """

    input: dict[str, Any]
    type: str = "tool_use"
    name: str = ""
    id: str = ""


@dataclass(frozen=True)
class LlmResponse:
    """Mimics an anthropic.types.Message for the subset curators use."""

    content: list[ToolUseBlock] = field(default_factory=list)
    model: str = ""
    stop_reason: str = "end_turn"
    usage: dict[str, int] = field(default_factory=dict)  # input_tokens, output_tokens, …
    total_cost_usd: float | None = None


class _MessagesAPI(Protocol):
    def create(self, **kwargs: Any) -> Any: ...


@runtime_checkable
class LlmClient(Protocol):
    """The minimum shape the curators speak.

    Real anthropic.Anthropic satisfies this. SubprocessClient also does.
    FakeAnthropic in tests also does.
    """

    messages: _MessagesAPI
