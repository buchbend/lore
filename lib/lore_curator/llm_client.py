from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any, Callable, Protocol, runtime_checkable
from dataclasses import dataclass, field


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
    """Mimics an anthropic.types.Message for the subset curators use.

    ``total_cost_usd`` is a lore-only extension absent on the real SDK type.
    """

    # Narrow shape used by the SubprocessClient synthesiser.  SDKClient does
    # NOT construct LlmResponse — it passes through the real SDK Message
    # object — so this typing is intentional and not a type gap.
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

    Note: ``@runtime_checkable`` only validates the *presence* of the
    ``messages`` attribute, not that ``messages.create`` is callable.
    Callers that need full validation should call the protocol explicitly
    (e.g. ``isinstance(client, LlmClient)`` is necessary but not sufficient).
    """

    messages: _MessagesAPI


class SDKClient:
    """LlmClient backend that delegates to anthropic.Anthropic.

    Holding an explicit class (rather than using a raw ``anthropic.Anthropic``
    directly) gives one seam to add cost telemetry, retries, or debug logging
    later, and keeps the factory symmetric with ``SubprocessClient``.
    """

    def __init__(self, *, api_key: str) -> None:
        import anthropic  # lazy — keeps anthropic an optional dep
        self._anthropic = anthropic.Anthropic(api_key=api_key)
        self.messages = self._anthropic.messages

    @property
    def backend_name(self) -> str:
        return "sdk"


# A subprocess runner has the same shape as subprocess.run for the kwargs we use.
SubprocessRunner = Callable[..., subprocess.CompletedProcess[str]]


class _SubprocessMessagesAPI:
    def __init__(
        self,
        *,
        binary: str,
        runner: SubprocessRunner,
        timeout_s: float,
    ):
        self._binary = binary
        self._runner = runner
        self._timeout_s = timeout_s

    def create(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
        max_tokens: int | None = None,   # accepted for API compat; ignored by claude -p
        **_extra: Any,
    ) -> LlmResponse:
        prompt = _extract_user_text(messages)
        schema = _resolve_tool_schema(tools, tool_choice)  # None if plain text

        cmd = [
            self._binary, "-p", prompt,
            "--output-format", "json",
            "--tools", "",
            "--model", model,
        ]
        if schema is not None:
            cmd += ["--json-schema", json.dumps(schema)]

        try:
            completed = self._runner(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout_s,
                check=False,
            )
        except FileNotFoundError as exc:
            raise LlmClientError(f"claude binary not found: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise LlmClientError(f"claude -p timed out after {self._timeout_s}s") from exc

        if completed.returncode != 0:
            raise LlmClientError(
                f"claude -p exit {completed.returncode}: "
                f"{(completed.stderr or '').strip()[:500]}"
            )

        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise LlmClientError(
                f"claude -p returned non-JSON: {completed.stdout[:200]!r}"
            ) from exc

        if payload.get("is_error"):
            raise LlmClientError(
                f"claude -p reported error: subtype={payload.get('subtype')!r}, "
                f"api={payload.get('api_error_status')!r}"
            )

        if schema is not None:
            structured = payload.get("structured_output")
            if not isinstance(structured, dict):
                raise LlmClientError(
                    "claude -p returned no structured_output despite --json-schema"
                )
            tool_name = (tool_choice or {}).get("name") or ""
            block = ToolUseBlock(input=structured, name=tool_name)
            content = [block]
        else:
            text = payload.get("result") or ""
            # NOTE: type="text" intentionally breaks the tool_use contract.
            # Plain-text path is present for factory symmetry only; curators do
            # not use it today, and any extractor filtering on type=="tool_use"
            # will correctly skip these blocks.
            content = [ToolUseBlock(input={"text": text}, type="text", name="")]

        return LlmResponse(
            content=content,
            model=payload.get("model", model),
            stop_reason=payload.get("stop_reason", "end_turn"),
            usage=(payload.get("usage") or {}),
            total_cost_usd=payload.get("total_cost_usd"),
        )


def _extract_user_text(messages: list[dict[str, Any]]) -> str:
    """Pull the single user-role text blob. Curators only ever send one user message."""
    if not messages:
        raise LlmClientError("messages=[] — nothing to send to claude -p")
    msg = messages[-1]
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        if not texts:
            raise LlmClientError("no text blocks found in user message content list")
        return "\n".join(texts)
    raise LlmClientError(f"unsupported message content type: {type(content)!r}")


def _resolve_tool_schema(
    tools: list[dict[str, Any]] | None,
    tool_choice: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Find the tool the caller picked and return its input_schema."""
    if not tools or not tool_choice or tool_choice.get("type") != "tool":
        return None
    want = tool_choice.get("name")
    for t in tools:
        if t.get("name") == want:
            schema = t.get("input_schema")
            if not isinstance(schema, dict):
                raise LlmClientError(
                    f"tool {want!r} has invalid input_schema "
                    f"(expected dict, got {type(schema).__name__!r})"
                )
            return schema
    raise LlmClientError(f"tool {want!r} not found in tools=[...]")


class SubprocessClient:
    """LlmClient backend that shells out to `claude -p`."""

    def __init__(
        self,
        *,
        binary: str = "claude",
        runner: SubprocessRunner | None = None,
        timeout_s: float = 120.0,
    ):
        self.messages = _SubprocessMessagesAPI(
            binary=binary,
            runner=runner if runner is not None else subprocess.run,
            timeout_s=timeout_s,
        )

    @property
    def backend_name(self) -> str:
        return "subprocess"

    @classmethod
    def is_available(cls, *, binary: str = "claude") -> bool:
        """Cheap PATH probe — used by make_llm_client to decide backend."""
        return shutil.which(binary) is not None


def make_llm_client(
    *,
    backend: str | None = None,
    api_key: str | None = None,
    binary: str = "claude",
) -> "LlmClient | None":
    """Select and return an LlmClient backend, or None if nothing is available.

    Resolution rules — first rule that applies wins:

    1. ``backend == "subscription"``: return SubprocessClient if ``binary``
       is on PATH, else raise LlmClientError.

    2. ``backend == "api"``: return SDKClient if ``api_key`` is truthy,
       else raise LlmClientError.

    3. ``backend`` is None or ``"auto"``: read ``LORE_LLM_BACKEND`` env var
       and apply rule 1 or 2 if it is set to ``"subscription"`` or ``"api"``.
       If the env var is unset or ``"auto"``, use auto-detection:
         - if SubprocessClient.is_available(binary=binary) → SubprocessClient
         - elif api_key → SDKClient
         - else → None   (caller should render "AI classification skipped")

    4. Any other ``backend`` string → ValueError (programmer error, not a
       runtime failure).

    Returning None is deliberately allowed.  Callers that receive None should
    reproduce the existing "AI classification skipped" warning with no
    behaviour change from Plans 1/2.

    Parameters
    ----------
    backend:
        ``"subscription"``, ``"api"``, ``"auto"``, or None.  None and
        ``"auto"`` are treated identically.
    api_key:
        Anthropic API key.  Both None and ``""`` are treated as absent.
    binary:
        Name or absolute path of the claude CLI binary (default ``"claude"``).
    """
    # Normalise explicit arg
    effective = (backend or "").strip().lower() or None

    if effective == "subscription":
        if SubprocessClient.is_available(binary=binary):
            return SubprocessClient(binary=binary)
        raise LlmClientError(
            "subscription backend requested but claude binary not on PATH"
        )

    if effective == "api":
        if api_key:
            return SDKClient(api_key=api_key)
        raise LlmClientError(
            "api backend requested but no ANTHROPIC_API_KEY provided"
        )

    if effective in (None, "auto"):
        env = os.environ.get("LORE_LLM_BACKEND", "").strip().lower()

        if env == "subscription":
            if SubprocessClient.is_available(binary=binary):
                return SubprocessClient(binary=binary)
            raise LlmClientError(
                "subscription backend requested but claude binary not on PATH"
            )

        if env == "api":
            if api_key:
                return SDKClient(api_key=api_key)
            raise LlmClientError(
                "api backend requested but no ANTHROPIC_API_KEY provided"
            )

        # env unset / "auto" — probe
        if SubprocessClient.is_available(binary=binary):
            return SubprocessClient(binary=binary)
        if api_key:
            return SDKClient(api_key=api_key)
        return None

    raise ValueError(
        f"unknown backend {backend!r}: expected 'subscription', 'api', 'auto', or None"
    )
