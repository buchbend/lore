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
    assert cmd[2] == "Is this noteworthy?"
    assert cmd[3:5] == ["--output-format", "json"]
    assert cmd[5:7] == ["--tools", ""]
    assert cmd[7:9] == ["--model", "claude-haiku-4-5-20251001"]
    assert cmd[9] == "--json-schema"
    assert json.loads(cmd[10])["properties"]["noteworthy"]["type"] == "boolean"
    assert len(cmd) == 11


def test_subprocess_passes_user_prompt_as_argv():
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

    assert seen_cmds[0][2] == "Is this noteworthy?"


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
