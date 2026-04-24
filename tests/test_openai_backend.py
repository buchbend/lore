"""Tests for the OpenAI-compatible curator backend.

Covers:
- OpenAICompatibleClient request translation (tools, tool_choice, model)
- OpenAICompatibleClient response translation (tool_calls → ToolUseBlock)
- make_llm_client dispatch with backend="openai"
- Env-var and root-config resolution
"""
from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fake openai SDK — minimal shape needed by OpenAICompatibleClient
# ---------------------------------------------------------------------------


class _FakeToolCall:
    def __init__(self, name: str, arguments: str, call_id: str = "call_1"):
        self.id = call_id
        self.type = "function"

        class _Fn:
            def __init__(self, n, a):
                self.name = n
                self.arguments = a

        self.function = _Fn(name, arguments)


class _FakeMessage:
    def __init__(self, content: str | None = None, tool_calls: list | None = None):
        self.content = content
        self.tool_calls = tool_calls
        self.role = "assistant"


class _FakeChoice:
    def __init__(self, message: _FakeMessage, finish_reason: str = "tool_calls"):
        self.message = message
        self.finish_reason = finish_reason


class _FakeUsage:
    def __init__(self, prompt_tokens: int = 100, completion_tokens: int = 20):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = prompt_tokens + completion_tokens


class _FakeCompletion:
    def __init__(self, choices: list[_FakeChoice], model: str = "test-model"):
        self.choices = choices
        self.model = model
        self.usage = _FakeUsage()


class _FakeChatCompletions:
    """Records the most recent create() kwargs so tests can assert translation."""

    def __init__(self, response: _FakeCompletion):
        self._response = response
        self.last_kwargs: dict | None = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return self._response


class _FakeChat:
    def __init__(self, completions: _FakeChatCompletions):
        self.completions = completions


class _FakeOpenAI:
    """Mimics openai.OpenAI(base_url=..., api_key=...)."""

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        # Default response: one tool_call matching "cluster" tool
        default_resp = _FakeCompletion(choices=[_FakeChoice(
            message=_FakeMessage(tool_calls=[_FakeToolCall(
                name="cluster",
                arguments=json.dumps({"clusters": [{"topic": "t", "scope": "s", "session_notes": []}]}),
            )]),
        )])
        self._completions = _FakeChatCompletions(default_resp)
        self.chat = _FakeChat(self._completions)


@pytest.fixture()
def fake_openai(monkeypatch: pytest.MonkeyPatch):
    """Install a fake openai module with OpenAI class."""
    fake_mod = types.ModuleType("openai")
    fake_mod.OpenAI = _FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake_mod)
    return fake_mod


# ---------------------------------------------------------------------------
# OpenAICompatibleClient
# ---------------------------------------------------------------------------


def test_openai_client_translates_tools_to_function_schema(fake_openai):
    from lore_curator.llm_client import OpenAICompatibleClient

    client = OpenAICompatibleClient(
        base_url="https://example.local/v1",
        api_key="sk-test",
        tier_to_model={"simple": "m-s", "middle": "m-m", "high": "m-h"},
    )

    anthropic_tool = {
        "name": "cluster",
        "description": "cluster session notes",
        "input_schema": {
            "type": "object",
            "properties": {"clusters": {"type": "array"}},
            "required": ["clusters"],
        },
    }

    client.messages.create(
        model="middle",
        max_tokens=1024,
        messages=[{"role": "user", "content": "hello"}],
        tools=[anthropic_tool],
        tool_choice={"type": "tool", "name": "cluster"},
    )

    kwargs = client._client._completions.last_kwargs
    assert kwargs is not None
    # Tier resolved to actual model
    assert kwargs["model"] == "m-m"
    # Tool translated to OpenAI function-calling format
    assert kwargs["tools"] == [{
        "type": "function",
        "function": {
            "name": "cluster",
            "description": "cluster session notes",
            "parameters": anthropic_tool["input_schema"],
        },
    }]
    # tool_choice translated to "required" — works on the widest range of
    # OpenAI-compatible endpoints (OSKI, vLLM, llama-cpp), which often
    # ignore the strict named-tool form.
    assert kwargs["tool_choice"] == "required"


def test_openai_client_translates_response_to_tool_use_block(fake_openai):
    from lore_curator.llm_client import OpenAICompatibleClient, ToolUseBlock

    client = OpenAICompatibleClient(
        base_url="https://example.local/v1",
        api_key="sk-test",
        tier_to_model={"simple": "m", "middle": "m", "high": "m"},
    )

    resp = client.messages.create(
        model="middle",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{
            "name": "cluster",
            "description": "",
            "input_schema": {"type": "object", "properties": {}},
        }],
        tool_choice={"type": "tool", "name": "cluster"},
    )

    # Response should be walkable like an Anthropic Message
    assert len(resp.content) == 1
    block = resp.content[0]
    assert isinstance(block, ToolUseBlock)
    assert block.type == "tool_use"
    assert block.name == "cluster"
    assert block.input == {"clusters": [{"topic": "t", "scope": "s", "session_notes": []}]}


def test_openai_client_passes_through_literal_model_id(fake_openai):
    """If model is not a tier name (simple/middle/high), it's passed as-is."""
    from lore_curator.llm_client import OpenAICompatibleClient

    client = OpenAICompatibleClient(
        base_url="https://example.local/v1",
        api_key="sk-test",
        tier_to_model={"simple": "m-s", "middle": "m-m", "high": "m-h"},
    )

    client.messages.create(
        model="Mistral Small 4 119B",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"name": "x", "description": "", "input_schema": {"type": "object"}}],
        tool_choice={"type": "tool", "name": "x"},
    )

    assert client._client._completions.last_kwargs["model"] == "Mistral Small 4 119B"


def test_openai_client_raises_on_missing_tool_call(fake_openai, monkeypatch):
    from lore_curator.llm_client import LlmClientError, OpenAICompatibleClient

    # Monkeypatch the response to have no tool_calls AND no parseable JSON
    client = OpenAICompatibleClient(
        base_url="https://example.local/v1",
        api_key="sk-test",
        tier_to_model={"simple": "m", "middle": "m", "high": "m"},
    )
    client._client._completions._response = _FakeCompletion(choices=[_FakeChoice(
        message=_FakeMessage(content="just text, no tool call, no json"),
        finish_reason="stop",
    )])

    with pytest.raises(LlmClientError, match="not parseable JSON"):
        client.messages.create(
            model="middle",
            messages=[{"role": "user", "content": "hi"}],
            tools=[{"name": "x", "description": "", "input_schema": {"type": "object"}}],
            tool_choice={"type": "tool", "name": "x"},
        )


# ---------------------------------------------------------------------------
# OSS-model fallback: JSON in content instead of tool_calls
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("content", [
    # Bare JSON
    '{"noteworthy": true, "reason": "work", "title": "t", "summary": "s"}',
    # ```json fenced
    '```json\n{"noteworthy": true, "reason": "work", "title": "t", "summary": "s"}\n```',
    # ``` unlabeled fenced
    '```\n{"noteworthy": true, "reason": "work", "title": "t", "summary": "s"}\n```',
    # With leading prose
    'Here is my answer:\n{"noteworthy": true, "reason": "work", "title": "t", "summary": "s"}',
    # With trailing prose
    '{"noteworthy": true, "reason": "work", "title": "t", "summary": "s"}\nThat was my answer.',
])
def test_openai_client_parses_json_in_content_when_tool_calls_empty(fake_openai, content):
    """OSKI / Mistral / OSS gateways often return the structured answer as
    plain text under tool_choice="required" rather than proper tool_calls."""
    from lore_curator.llm_client import OpenAICompatibleClient, ToolUseBlock

    client = OpenAICompatibleClient(
        base_url="https://example.local/v1",
        api_key="sk-test",
        tier_to_model={"simple": "m", "middle": "m", "high": "m"},
    )
    client._client._completions._response = _FakeCompletion(choices=[_FakeChoice(
        message=_FakeMessage(content=content, tool_calls=[]),
        finish_reason="stop",
    )])

    resp = client.messages.create(
        model="middle",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{
            "name": "classify",
            "description": "",
            "input_schema": {"type": "object", "properties": {}},
        }],
        tool_choice={"type": "tool", "name": "classify"},
    )

    assert len(resp.content) == 1
    block = resp.content[0]
    assert isinstance(block, ToolUseBlock)
    assert block.type == "tool_use"
    assert block.name == "classify"
    assert block.input["noteworthy"] is True
    assert block.input["reason"] == "work"
    assert block.input["summary"] == "s"


def test_parse_content_as_tool_args_handles_edge_cases():
    from lore_curator.llm_client import _parse_content_as_tool_args

    assert _parse_content_as_tool_args("") is None
    assert _parse_content_as_tool_args("   ") is None
    assert _parse_content_as_tool_args("no json here") is None
    assert _parse_content_as_tool_args("{invalid json}") is None
    # Nested objects
    result = _parse_content_as_tool_args('{"a": {"b": "c"}, "d": [1, 2]}')
    assert result == {"a": {"b": "c"}, "d": [1, 2]}
    # JSON string with escaped braces inside
    result = _parse_content_as_tool_args('{"text": "this has \\"quotes\\" and {braces}"}')
    assert result == {"text": 'this has "quotes" and {braces}'}


def test_openai_client_backend_name(fake_openai):
    from lore_curator.llm_client import OpenAICompatibleClient

    client = OpenAICompatibleClient(
        base_url="https://example.local/v1",
        api_key="sk-test",
        tier_to_model={"simple": "m", "middle": "m", "high": "m"},
    )
    assert client.backend_name == "openai"


# ---------------------------------------------------------------------------
# Claude-family → tier inverse heuristic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("claude_model,expected_tier,expected_openai_model", [
    ("claude-haiku-4-5", "simple", "oss-s"),
    ("claude-sonnet-4-6", "middle", "oss-m"),
    ("claude-opus-4-7", "high", "oss-h"),
    # Version-agnostic: catch haiku/sonnet/opus regardless of minor version
    ("claude-haiku-4-6", "simple", "oss-s"),
    ("claude-sonnet-5-0", "middle", "oss-m"),
])
def test_openai_client_inverts_claude_family_name_to_tier(
    fake_openai, claude_model, expected_tier, expected_openai_model,
):
    """Curators resolve tier→claude-ID before calling the client. OpenAI client
    reverses that: if a Claude family name comes in, route via tier_to_model."""
    from lore_curator.llm_client import OpenAICompatibleClient

    client = OpenAICompatibleClient(
        base_url="https://example.local/v1",
        api_key="sk-test",
        tier_to_model={"simple": "oss-s", "middle": "oss-m", "high": "oss-h"},
    )

    client.messages.create(
        model=claude_model,
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"name": "x", "description": "", "input_schema": {"type": "object"}}],
        tool_choice={"type": "tool", "name": "x"},
    )

    assert client._client._completions.last_kwargs["model"] == expected_openai_model


def test_openai_client_raises_when_claude_model_has_no_tier_mapping(fake_openai):
    """Caller passed claude-sonnet-4-6 but no model_middle configured — error
    early with a helpful message rather than 404-ing against the endpoint."""
    from lore_curator.llm_client import LlmClientError, OpenAICompatibleClient

    client = OpenAICompatibleClient(
        base_url="https://example.local/v1",
        api_key="sk-test",
        tier_to_model={},  # nothing configured
    )

    with pytest.raises(LlmClientError, match="LORE_OPENAI_MODEL_MIDDLE"):
        client.messages.create(
            model="claude-sonnet-4-6",
            messages=[{"role": "user", "content": "hi"}],
            tools=[{"name": "x", "description": "", "input_schema": {"type": "object"}}],
            tool_choice={"type": "tool", "name": "x"},
        )


# ---------------------------------------------------------------------------
# Subprocess client timeout configurability
# ---------------------------------------------------------------------------


def test_subprocess_client_default_timeout_is_300s():
    from lore_curator.llm_client import SubprocessClient

    client = SubprocessClient()
    assert client.messages._timeout_s == 300.0


def test_subprocess_client_reads_timeout_env(monkeypatch):
    from lore_curator.llm_client import SubprocessClient

    monkeypatch.setenv("LORE_CLAUDE_TIMEOUT_S", "600")
    client = SubprocessClient()
    assert client.messages._timeout_s == 600.0


def test_subprocess_client_ignores_bogus_timeout_env(monkeypatch):
    from lore_curator.llm_client import SubprocessClient

    monkeypatch.setenv("LORE_CLAUDE_TIMEOUT_S", "not-a-number")
    client = SubprocessClient()
    assert client.messages._timeout_s == 300.0  # falls back to default


# ---------------------------------------------------------------------------
# make_llm_client dispatch
# ---------------------------------------------------------------------------


def test_make_llm_client_openai_from_env(monkeypatch, fake_openai):
    from lore_curator.llm_client import OpenAICompatibleClient, make_llm_client

    monkeypatch.setenv("LORE_OPENAI_BASE_URL", "https://example.local/v1")
    monkeypatch.setenv("LORE_OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("LORE_OPENAI_MODEL_MIDDLE", "gpt-oss-120b")

    client = make_llm_client(backend="openai")
    assert isinstance(client, OpenAICompatibleClient)
    assert client._tier_to_model["middle"] == "gpt-oss-120b"


def test_make_llm_client_openai_missing_base_url_raises(monkeypatch):
    from lore_curator.llm_client import LlmClientError, make_llm_client

    monkeypatch.delenv("LORE_OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("LORE_OPENAI_API_KEY", "sk-test")

    with pytest.raises(LlmClientError, match="LORE_OPENAI_BASE_URL"):
        make_llm_client(backend="openai")


def test_make_llm_client_openai_missing_api_key_raises(monkeypatch):
    from lore_curator.llm_client import LlmClientError, make_llm_client

    monkeypatch.setenv("LORE_OPENAI_BASE_URL", "https://example.local/v1")
    monkeypatch.delenv("LORE_OPENAI_API_KEY", raising=False)

    with pytest.raises(LlmClientError, match="LORE_OPENAI_API_KEY"):
        make_llm_client(backend="openai")


def test_make_llm_client_env_var_openai(monkeypatch, fake_openai):
    """LORE_LLM_BACKEND=openai picks OpenAI backend without explicit argument."""
    from lore_curator.llm_client import OpenAICompatibleClient, make_llm_client

    monkeypatch.setenv("LORE_LLM_BACKEND", "openai")
    monkeypatch.setenv("LORE_OPENAI_BASE_URL", "https://example.local/v1")
    monkeypatch.setenv("LORE_OPENAI_API_KEY", "sk-test")
    # Don't let claude binary on PATH win
    monkeypatch.setattr("shutil.which", lambda name: None)

    client = make_llm_client()
    assert isinstance(client, OpenAICompatibleClient)


def test_make_llm_client_rejects_unknown_backend():
    from lore_curator.llm_client import make_llm_client

    with pytest.raises(ValueError, match="unknown backend"):
        make_llm_client(backend="bogus")


# ---------------------------------------------------------------------------
# Root config integration
# ---------------------------------------------------------------------------


def test_root_config_curator_defaults(tmp_path: Path):
    from lore_core.root_config import load_root_config

    cfg = load_root_config(tmp_path)
    assert cfg.curator.backend == "auto"
    assert cfg.curator.openai.base_url == ""
    assert cfg.curator.openai.api_key_env == "LORE_OPENAI_API_KEY"


def test_root_config_curator_parses_yaml(tmp_path: Path):
    from lore_core.root_config import load_root_config

    cfg_dir = tmp_path / ".lore"
    cfg_dir.mkdir()
    (cfg_dir / "config.yml").write_text(
        "curator:\n"
        "  backend: openai\n"
        "  openai:\n"
        "    base_url: https://chat.kiconnect.nrw/api/v1\n"
        "    api_key_env: OSKI_API_KEY\n"
        "    model_middle: Mistral Small 4 119B 2603 KI:EZ\n"
        "    model_high: Openai GPT OSS 120B\n"
    )

    cfg = load_root_config(tmp_path)
    assert cfg.curator.backend == "openai"
    assert cfg.curator.openai.base_url == "https://chat.kiconnect.nrw/api/v1"
    assert cfg.curator.openai.api_key_env == "OSKI_API_KEY"
    assert cfg.curator.openai.model_middle == "Mistral Small 4 119B 2603 KI:EZ"


# ---------------------------------------------------------------------------
# CLI backend resolution (cli flag > env > config > auto)
# ---------------------------------------------------------------------------


def test_resolve_backend_cli_flag_wins(tmp_path: Path, monkeypatch):
    from lore_curator.curator_c import _resolve_backend

    monkeypatch.setenv("LORE_LLM_BACKEND", "subscription")
    # Config says "api" but CLI flag "openai" wins.
    cfg_dir = tmp_path / ".lore"
    cfg_dir.mkdir()
    (cfg_dir / "config.yml").write_text("curator:\n  backend: api\n")

    assert _resolve_backend("openai", tmp_path) == "openai"


def test_resolve_backend_env_overrides_config(tmp_path: Path, monkeypatch):
    from lore_curator.curator_c import _resolve_backend

    monkeypatch.setenv("LORE_LLM_BACKEND", "subscription")
    cfg_dir = tmp_path / ".lore"
    cfg_dir.mkdir()
    (cfg_dir / "config.yml").write_text("curator:\n  backend: openai\n")

    assert _resolve_backend(None, tmp_path) == "subscription"


def test_resolve_backend_config_used_when_no_env_or_cli(tmp_path: Path, monkeypatch):
    from lore_curator.curator_c import _resolve_backend

    monkeypatch.delenv("LORE_LLM_BACKEND", raising=False)
    cfg_dir = tmp_path / ".lore"
    cfg_dir.mkdir()
    (cfg_dir / "config.yml").write_text("curator:\n  backend: openai\n")

    assert _resolve_backend(None, tmp_path) == "openai"


def test_resolve_backend_defaults_to_none(tmp_path: Path, monkeypatch):
    """No CLI, no env, no config → None (i.e. auto-detect)."""
    from lore_curator.curator_c import _resolve_backend

    monkeypatch.delenv("LORE_LLM_BACKEND", raising=False)
    assert _resolve_backend(None, tmp_path) is None


def test_curator_run_cli_backend_flag_openai(tmp_path: Path, monkeypatch, fake_openai):
    """`lore curator run --backend openai --dry-run` announces OpenAI backend."""
    from typer.testing import CliRunner

    from lore_cli.__main__ import app

    lore_root = tmp_path / "lore_root"
    lore_root.mkdir()
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    monkeypatch.delenv("LORE_LLM_BACKEND", raising=False)
    monkeypatch.setenv("LORE_OPENAI_BASE_URL", "https://example.local/v1")
    monkeypatch.setenv("LORE_OPENAI_API_KEY", "sk-test")

    # Stub Curator A so we don't need a real pipeline.
    from dataclasses import dataclass, field

    @dataclass
    class _FakeResult:
        transcripts_considered: int = 0
        noteworthy_count: int = 0
        new_notes: list = field(default_factory=list)
        merged_notes: list = field(default_factory=list)
        skipped_reasons: dict = field(default_factory=dict)
        duration_seconds: float = 0.0

    monkeypatch.setattr(
        "lore_curator.curator_a.run_curator_a",
        lambda **kwargs: _FakeResult(),
    )

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(app, ["curator", "run", "--dry-run", "--backend", "openai"])
    assert result.exit_code == 0, result.output + (result.stderr or "")
    assert "OpenAI-compatible endpoint" in result.output
