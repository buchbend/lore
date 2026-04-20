"""Happy-path tests for SubprocessClient (T3).

All tests use a fake runner — no real `claude` binary is invoked.
"""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from lore_curator.llm_client import (
    LlmResponse,
    SubprocessClient,
    ToolUseBlock,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TOOLS = [
    {
        "name": "classify",
        "input_schema": {
            "type": "object",
            "properties": {
                "noteworthy": {"type": "boolean"},
                "reason": {"type": "string"},
                "title": {"type": "string"},
            },
            "required": ["noteworthy", "reason", "title"],
        },
    }
]
_TOOL_CHOICE = {"type": "tool", "name": "classify"}
_MODEL = "claude-haiku-4-5-20251001"
_MESSAGES = [{"role": "user", "content": "Is this noteworthy?"}]

_FAKE_PAYLOAD = {
    "is_error": False,
    "structured_output": {"noteworthy": True, "reason": "x", "title": "t"},
    "usage": {"input_tokens": 5, "output_tokens": 3},
    "total_cost_usd": 0.0001,
    "model": "claude-haiku-4-5-20251001",
    "stop_reason": "end_turn",
}


def make_runner(payload: dict, *, returncode: int = 0, stderr: str = ""):
    def _runner(cmd, **kwargs):
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=returncode,
            stdout=json.dumps(payload),
            stderr=stderr,
        )
    return _runner


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_subprocess_builds_expected_cmdline():
    seen_cmds: list[list[str]] = []

    def _runner(cmd, **kw):
        seen_cmds.append(cmd)
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout=json.dumps(_FAKE_PAYLOAD),
            stderr="",
        )

    client = SubprocessClient(runner=_runner)
    client.messages.create(
        model=_MODEL,
        messages=_MESSAGES,
        tools=_TOOLS,
        tool_choice=_TOOL_CHOICE,
    )

    assert len(seen_cmds) == 1
    cmd = seen_cmds[0]

    assert cmd[0] == "claude"
    assert cmd[1] == "-p"
    assert cmd[2] == ""  # prompt moved to stdin to avoid OS argv size limit
    assert cmd[3:5] == ["--output-format", "json"]
    assert cmd[5:7] == ["--tools", ""]
    assert cmd[7:9] == ["--model", "claude-haiku-4-5-20251001"]
    assert cmd[9] == "--json-schema"
    assert json.loads(cmd[10])["properties"]["noteworthy"]["type"] == "boolean"
    assert len(cmd) == 11


def test_subprocess_passes_user_prompt_on_stdin():
    seen_kwargs: list[dict] = []

    def _runner(cmd, **kw):
        seen_kwargs.append(kw)
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout=json.dumps(_FAKE_PAYLOAD),
            stderr="",
        )

    client = SubprocessClient(runner=_runner)
    client.messages.create(
        model=_MODEL,
        messages=_MESSAGES,
        tools=_TOOLS,
        tool_choice=_TOOL_CHOICE,
    )

    assert seen_kwargs[0]["input"] == "Is this noteworthy?"


def test_subprocess_parses_structured_output_to_tool_use_block():
    client = SubprocessClient(runner=make_runner(_FAKE_PAYLOAD))
    result = client.messages.create(
        model=_MODEL,
        messages=_MESSAGES,
        tools=_TOOLS,
        tool_choice=_TOOL_CHOICE,
    )

    assert isinstance(result, LlmResponse)
    assert len(result.content) == 1
    block = result.content[0]
    assert isinstance(block, ToolUseBlock)
    assert block.input == {"noteworthy": True, "reason": "x", "title": "t"}


def test_subprocess_surfaces_usage_and_cost():
    client = SubprocessClient(runner=make_runner(_FAKE_PAYLOAD))
    resp = client.messages.create(
        model=_MODEL,
        messages=_MESSAGES,
        tools=_TOOLS,
        tool_choice=_TOOL_CHOICE,
    )

    assert resp.usage["input_tokens"] == 5
    assert resp.total_cost_usd == 0.0001


def test_subprocess_passes_model_name_through():
    seen_cmds: list[list[str]] = []

    def _runner(cmd, **kw):
        seen_cmds.append(cmd)
        payload = {**_FAKE_PAYLOAD, "model": "claude-sonnet-4-6"}
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )

    client = SubprocessClient(runner=_runner)
    client.messages.create(
        model="claude-sonnet-4-6",
        messages=_MESSAGES,
        tools=_TOOLS,
        tool_choice=_TOOL_CHOICE,
    )

    cmd = seen_cmds[0]
    assert "--model" in cmd
    idx = cmd.index("--model")
    assert cmd[idx:idx + 2] == ["--model", "claude-sonnet-4-6"]


def test_subprocess_is_available_honours_path(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/claude")
    assert SubprocessClient.is_available(binary="claude") is True

    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert SubprocessClient.is_available(binary="claude") is False


# ---------------------------------------------------------------------------
# Error-path tests (T4)
# ---------------------------------------------------------------------------

def test_subprocess_raises_llmclienterror_on_nonzero_exit():
    from lore_curator.llm_client import LlmClientError

    def _runner(cmd, **kw):
        return subprocess.CompletedProcess(
            args=cmd, returncode=2, stdout="", stderr="auth failed\n"
        )

    client = SubprocessClient(runner=_runner)
    with pytest.raises(LlmClientError) as excinfo:
        client.messages.create(model=_MODEL, messages=_MESSAGES, tools=_TOOLS, tool_choice=_TOOL_CHOICE)

    msg = str(excinfo.value)
    assert "exit 2" in msg
    assert "auth failed" in msg


def test_subprocess_raises_on_malformed_json():
    from lore_curator.llm_client import LlmClientError

    def _runner(cmd, **kw):
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="<<not json>>", stderr=""
        )

    client = SubprocessClient(runner=_runner)
    with pytest.raises(LlmClientError) as excinfo:
        client.messages.create(model=_MODEL, messages=_MESSAGES, tools=_TOOLS, tool_choice=_TOOL_CHOICE)

    assert "non-JSON" in str(excinfo.value)


def test_subprocess_raises_on_api_error_payload():
    from lore_curator.llm_client import LlmClientError

    error_payload = {
        "is_error": True,
        "subtype": "error_rate_limit",
        "api_error_status": 429,
        "structured_output": None,
    }

    client = SubprocessClient(runner=make_runner(error_payload))
    with pytest.raises(LlmClientError) as excinfo:
        client.messages.create(model=_MODEL, messages=_MESSAGES, tools=_TOOLS, tool_choice=_TOOL_CHOICE)

    msg = str(excinfo.value)
    assert "subtype" in msg
    assert "429" in msg


def test_subprocess_raises_on_missing_structured_output():
    from lore_curator.llm_client import LlmClientError

    payload = {**_FAKE_PAYLOAD, "structured_output": None}

    client = SubprocessClient(runner=make_runner(payload))
    with pytest.raises(LlmClientError) as excinfo:
        client.messages.create(model=_MODEL, messages=_MESSAGES, tools=_TOOLS, tool_choice=_TOOL_CHOICE)

    assert "structured_output" in str(excinfo.value)


def test_subprocess_raises_on_timeout():
    from lore_curator.llm_client import LlmClientError

    def _runner(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=0.1)

    client = SubprocessClient(runner=_runner)
    with pytest.raises(LlmClientError) as excinfo:
        client.messages.create(model=_MODEL, messages=_MESSAGES, tools=_TOOLS, tool_choice=_TOOL_CHOICE)

    assert "timed out" in str(excinfo.value)


def test_subprocess_raises_on_missing_binary():
    from lore_curator.llm_client import LlmClientError

    def _runner(cmd, **kw):
        raise FileNotFoundError(cmd[0])

    client = SubprocessClient(runner=_runner)
    with pytest.raises(LlmClientError) as excinfo:
        client.messages.create(model=_MODEL, messages=_MESSAGES, tools=_TOOLS, tool_choice=_TOOL_CHOICE)

    assert "claude binary not found" in str(excinfo.value)


def test_subprocess_raises_on_unknown_tool_name():
    from lore_curator.llm_client import LlmClientError

    def _sentinel_runner(cmd, **kw):
        raise AssertionError("runner should not be called for unknown tool name")

    client = SubprocessClient(runner=_sentinel_runner)
    with pytest.raises(LlmClientError) as excinfo:
        client.messages.create(
            model=_MODEL,
            messages=_MESSAGES,
            tools=_TOOLS,
            tool_choice={"type": "tool", "name": "nope"},
        )

    assert "'nope' not found" in str(excinfo.value)


def test_subprocess_raises_on_invalid_tool_schema_type():
    from lore_curator.llm_client import LlmClientError

    bad_tools = [{"name": "classify", "input_schema": "not-a-dict"}]

    def _sentinel_runner(cmd, **kw):
        raise AssertionError("runner should not be called for invalid schema type")

    client = SubprocessClient(runner=_sentinel_runner)
    with pytest.raises(LlmClientError) as excinfo:
        client.messages.create(
            model=_MODEL,
            messages=_MESSAGES,
            tools=bad_tools,
            tool_choice={"type": "tool", "name": "classify"},
        )

    msg = str(excinfo.value)
    assert "invalid input_schema" in msg
    assert "'str'" in msg
