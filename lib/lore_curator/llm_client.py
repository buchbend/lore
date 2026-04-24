from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
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

        # Pipe prompt via stdin — passing as argv hits the OS argv size limit
        # (~128KB on Linux) for real transcripts. Keep a stub positional slot
        # so existing positional-assertion tests still find flags at the
        # expected indices.
        cmd = [
            self._binary, "-p", "",
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
                input=prompt,
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


_DEFAULT_CLAUDE_TIMEOUT_S = 300.0


def _resolve_claude_timeout(explicit: float | None) -> float:
    """Pick subprocess timeout: explicit arg > LORE_CLAUDE_TIMEOUT_S env > default."""
    if explicit is not None:
        return explicit
    raw = os.environ.get("LORE_CLAUDE_TIMEOUT_S", "").strip()
    if raw:
        try:
            parsed = float(raw)
            if parsed > 0:
                return parsed
        except ValueError:
            pass
    return _DEFAULT_CLAUDE_TIMEOUT_S


class SubprocessClient:
    """LlmClient backend that shells out to `claude -p`.

    Default timeout is 300s — large transcripts (100k+ chars) regularly push
    past the old 120s limit. Override via ``timeout_s`` kwarg or the env var
    ``LORE_CLAUDE_TIMEOUT_S``.
    """

    def __init__(
        self,
        *,
        binary: str = "claude",
        runner: SubprocessRunner | None = None,
        timeout_s: float | None = None,
    ):
        self.messages = _SubprocessMessagesAPI(
            binary=binary,
            runner=runner if runner is not None else subprocess.run,
            timeout_s=_resolve_claude_timeout(timeout_s),
        )

    @property
    def backend_name(self) -> str:
        return "subprocess"

    @classmethod
    def is_available(cls, *, binary: str = "claude") -> bool:
        """Cheap PATH probe — used by make_llm_client to decide backend."""
        return shutil.which(binary) is not None


_CLAUDE_FAMILY_TIERS = (
    ("haiku", "simple"),
    ("sonnet", "middle"),
    ("opus", "high"),
)


def _infer_tier(model: str) -> str | None:
    """Best-effort reverse of wiki_config ModelsConfig.

    Curators resolve tier→Claude model ID via wiki_config.yml before calling
    the LLM client (e.g. tier "middle" → "claude-sonnet-4-6"). When routing
    through an OpenAI-compatible backend, we invert: look for
    haiku/sonnet/opus in the model string to find the tier, then apply
    the user's OpenAI-side mapping.
    """
    if not isinstance(model, str):
        return None
    if model in ("simple", "middle", "high"):
        return model
    lower = model.lower()
    if not lower.startswith("claude"):
        return None
    for needle, tier in _CLAUDE_FAMILY_TIERS:
        if needle in lower:
            return tier
    return None


class _OpenAIMessagesAPI:
    """Translates Anthropic-style messages.create(...) to OpenAI chat.completions.

    Curators send a single-tool tool_choice and expect a ToolUseBlock response.
    OpenAI function-calling returns tool_calls with a JSON-string arguments field,
    so we JSON-decode and wrap into a ToolUseBlock to satisfy the curator contract.

    Model resolution order:
      1. If ``model`` is a tier name or a Claude family name, map to tier via
         :func:`_infer_tier`, then look up in ``tier_to_model``.
      2. If ``model`` is a literal (non-Claude) ID, pass through.
      3. If a tier is inferred but has no entry in ``tier_to_model``, raise
         LlmClientError with a message pointing at LORE_OPENAI_MODEL_*.
    """

    def __init__(self, client: Any, tier_to_model: dict[str, str]):
        self._client = client
        self._tier_to_model = tier_to_model

    def create(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
        max_tokens: int | None = None,
        **_extra: Any,
    ) -> LlmResponse:
        tier = _infer_tier(model)
        if tier is not None:
            configured = self._tier_to_model.get(tier)
            if configured:
                resolved_model = configured
            else:
                raise LlmClientError(
                    f"openai backend: no model configured for tier {tier!r} "
                    f"(received {model!r}). Set "
                    f"LORE_OPENAI_MODEL_{tier.upper()} "
                    f"or curator.openai.model_{tier} in .lore/config.yml."
                )
        else:
            resolved_model = model

        openai_tools = None
        openai_tool_choice = None
        if tools:
            openai_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema", {"type": "object"}),
                    },
                }
                for t in tools
            ]
        if tool_choice and tool_choice.get("type") == "tool":
            openai_tool_choice = {
                "type": "function",
                "function": {"name": tool_choice.get("name", "")},
            }

        kwargs: dict[str, Any] = {
            "model": resolved_model,
            "messages": messages,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if openai_tools is not None:
            kwargs["tools"] = openai_tools
        if openai_tool_choice is not None:
            kwargs["tool_choice"] = openai_tool_choice

        try:
            completion = self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise LlmClientError(f"openai-compatible call failed: {exc}") from exc

        choices = getattr(completion, "choices", None) or []
        if not choices:
            raise LlmClientError("openai-compatible response has no choices")
        msg = choices[0].message
        tool_calls = getattr(msg, "tool_calls", None) or []

        content: list[ToolUseBlock]
        if tool_calls:
            tc = tool_calls[0]
            name = getattr(tc.function, "name", "")
            raw_args = getattr(tc.function, "arguments", "") or "{}"
            try:
                parsed = json.loads(raw_args)
            except json.JSONDecodeError as exc:
                raise LlmClientError(
                    f"openai tool_call arguments not JSON: {raw_args[:200]!r}"
                ) from exc
            if not isinstance(parsed, dict):
                raise LlmClientError(
                    f"openai tool_call arguments not a JSON object: {type(parsed).__name__}"
                )
            content = [ToolUseBlock(
                input=parsed,
                name=name,
                id=getattr(tc, "id", "") or "",
            )]
        else:
            if tool_choice:
                raise LlmClientError(
                    "openai response has no tool_call despite tool_choice — "
                    f"finish_reason={getattr(choices[0], 'finish_reason', '?')}"
                )
            # Plain-text fallback (curators don't use this today).
            text = getattr(msg, "content", "") or ""
            content = [ToolUseBlock(input={"text": text}, type="text", name="")]

        usage_obj = getattr(completion, "usage", None)
        usage_dict: dict[str, int] = {}
        if usage_obj is not None:
            for attr, key in (
                ("prompt_tokens", "input_tokens"),
                ("completion_tokens", "output_tokens"),
                ("total_tokens", "total_tokens"),
            ):
                val = getattr(usage_obj, attr, None)
                if isinstance(val, int):
                    usage_dict[key] = val

        return LlmResponse(
            content=content,
            model=getattr(completion, "model", resolved_model) or resolved_model,
            stop_reason=getattr(choices[0], "finish_reason", "end_turn") or "end_turn",
            usage=usage_dict,
        )


class OpenAICompatibleClient:
    """LlmClient backend that wraps openai.OpenAI(base_url, api_key).

    Works with any OpenAI-compatible endpoint (local model gateways, OSS
    inference servers, OpenRouter, etc.). Translates the curator's
    Anthropic-style ``messages.create(...)`` call into OpenAI chat
    completions with function-calling and converts the tool_call response
    back into a ToolUseBlock so downstream curator code is unchanged.

    Tier names (``simple``/``middle``/``high``) are resolved via
    ``tier_to_model``; literal model IDs are passed through unchanged.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        tier_to_model: dict[str, str] | None = None,
    ) -> None:
        import openai  # lazy — optional dep
        self._client = openai.OpenAI(base_url=base_url, api_key=api_key)
        self._tier_to_model = tier_to_model or {}
        self.messages = _OpenAIMessagesAPI(self._client, self._tier_to_model)

    @property
    def backend_name(self) -> str:
        return "openai"


_ALLOWED_BACKEND_ARGS = frozenset({"subscription", "api", "openai", "auto"})


def _normalize_backend_arg(backend: str | None) -> str | None:
    """Normalize the explicit ``backend`` kwarg.

    - ``None`` → None (triggers auto-detect).
    - Whitespace is stripped.
    - Case-sensitive match against ``{"subscription", "api", "auto"}``.
    - Anything else returns the stripped string verbatim so the caller
      can raise ``ValueError`` with the original-ish value.
    """
    if backend is None:
        return None
    stripped = backend.strip()
    if stripped == "":
        return None
    # Case-sensitive — programmer-facing API
    return stripped


def _make_subprocess_client(binary: str) -> "SubprocessClient":
    """Return a SubprocessClient or raise LlmClientError if binary is absent."""
    if SubprocessClient.is_available(binary=binary):
        return SubprocessClient(binary=binary)
    raise LlmClientError(
        "subscription backend requested but claude binary not on PATH"
    )


def _make_sdk_client(api_key: str | None) -> "SDKClient":
    """Return an SDKClient or raise LlmClientError if api_key is absent."""
    if api_key:
        return SDKClient(api_key=api_key)
    raise LlmClientError(
        "api backend requested but no ANTHROPIC_API_KEY provided"
    )


def _resolve_openai_settings(
    lore_root: "Path | None" = None,
) -> tuple[str, str, dict[str, str]]:
    """Resolve base_url, api_key, and tier→model map from env + root config.

    Precedence per field: env var > root config > empty.
    Raises LlmClientError if base_url or api_key can't be found.
    """
    base_url = os.environ.get("LORE_OPENAI_BASE_URL", "").strip()
    api_key_env_name = "LORE_OPENAI_API_KEY"
    tier_to_model: dict[str, str] = {}

    if lore_root is not None:
        try:
            from lore_core.root_config import load_root_config
            cfg = load_root_config(lore_root).curator.openai
            if not base_url and cfg.base_url:
                base_url = cfg.base_url.strip()
            if cfg.api_key_env:
                api_key_env_name = cfg.api_key_env
            if cfg.model_simple:
                tier_to_model["simple"] = cfg.model_simple
            if cfg.model_middle:
                tier_to_model["middle"] = cfg.model_middle
            if cfg.model_high:
                tier_to_model["high"] = cfg.model_high
        except Exception:
            pass

    # Env-var model overrides always win over config.
    for tier, env_name in (
        ("simple", "LORE_OPENAI_MODEL_SIMPLE"),
        ("middle", "LORE_OPENAI_MODEL_MIDDLE"),
        ("high", "LORE_OPENAI_MODEL_HIGH"),
    ):
        val = os.environ.get(env_name, "").strip()
        if val:
            tier_to_model[tier] = val

    if not base_url:
        raise LlmClientError(
            "openai backend requested but LORE_OPENAI_BASE_URL is not set "
            "(and no curator.openai.base_url in .lore/config.yml)"
        )

    api_key = os.environ.get(api_key_env_name, "").strip()
    if not api_key:
        raise LlmClientError(
            f"openai backend requested but {api_key_env_name} is not set"
        )

    return base_url, api_key, tier_to_model


def _make_openai_client(lore_root: "Path | None" = None) -> "OpenAICompatibleClient":
    base_url, api_key, tier_to_model = _resolve_openai_settings(lore_root)
    return OpenAICompatibleClient(
        base_url=base_url,
        api_key=api_key,
        tier_to_model=tier_to_model,
    )


def make_llm_client(
    *,
    backend: str | None = None,
    api_key: str | None = None,
    binary: str = "claude",
    lore_root: "Path | None" = None,
) -> "LlmClient | None":
    """Select and return an LlmClient backend, or None if nothing is available.

    Resolution rules — first rule that applies wins:

    1. ``backend == "subscription"``: return SubprocessClient if ``binary``
       is on PATH, else raise LlmClientError.

    2. ``backend == "api"``: return SDKClient if ``api_key`` is truthy,
       else raise LlmClientError.

    3. ``backend == "openai"``: return OpenAICompatibleClient resolved from
       env vars (LORE_OPENAI_BASE_URL, LORE_OPENAI_API_KEY, LORE_OPENAI_MODEL_*)
       and, if ``lore_root`` is given, ``.lore/config.yml`` curator.openai
       settings. Raises LlmClientError on missing base_url or api key.

    4. ``backend`` is None or ``"auto"``: read ``LORE_LLM_BACKEND`` env var
       (case-insensitive — shell-facing) and dispatch if set to a known backend.
       If unset or ``"auto"``, use auto-detection:
         - if SubprocessClient.is_available(binary=binary) → SubprocessClient
         - elif api_key → SDKClient
         - else → None   (caller should render "AI classification skipped")

    5. Any other ``backend`` string → ValueError.

    Parameters
    ----------
    backend:
        ``"subscription"``, ``"api"``, ``"openai"``, ``"auto"``, or None.
        None and ``"auto"`` are treated identically.  **Case-sensitive** —
        use the env var ``LORE_LLM_BACKEND`` for case-insensitive shell input.
    api_key:
        Anthropic API key.  Both None and ``""`` are treated as absent.
    binary:
        Name or absolute path of the claude CLI binary (default ``"claude"``).
    lore_root:
        Optional Lore root for reading ``.lore/config.yml``. When absent,
        only env vars are consulted for OpenAI settings.
    """
    effective = _normalize_backend_arg(backend)

    if effective is not None and effective not in _ALLOWED_BACKEND_ARGS:
        raise ValueError(
            f"unknown backend {backend!r} "
            "(expected 'subscription', 'api', 'openai', 'auto', or None)"
        )

    if effective == "subscription":
        return _make_subprocess_client(binary)

    if effective == "api":
        return _make_sdk_client(api_key)

    if effective == "openai":
        return _make_openai_client(lore_root)

    if effective in (None, "auto"):
        env = os.environ.get("LORE_LLM_BACKEND", "").strip().lower()

        if env == "subscription":
            return _make_subprocess_client(binary)
        if env == "api":
            return _make_sdk_client(api_key)
        if env == "openai":
            return _make_openai_client(lore_root)

        # env unset / "auto" — probe
        if SubprocessClient.is_available(binary=binary):
            return SubprocessClient(binary=binary)
        if api_key:
            return SDKClient(api_key=api_key)
        return None

    raise ValueError(  # pragma: no cover
        f"unknown backend {backend!r}"
    )
