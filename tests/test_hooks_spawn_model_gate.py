"""`lore hook spawn-model-gate` — PreToolUse CLI wiring for the spawn gate.

Covers the stdin/exit-code/stderr plumbing around
lore_core.spawn_gate.check_spawn(): denial protocol is exit 2 + stderr
message, allow is exit 0 + silent, and malformed stdin fails open.
"""

from __future__ import annotations

import json

from lore_cli.hooks import hook_app
from typer.testing import CliRunner


def _invoke(payload) -> object:
    runner = CliRunner()
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return runner.invoke(hook_app, ["spawn-model-gate"], input=stdin)


def test_denies_model_less_spawn() -> None:
    result = _invoke({"tool_name": "Task", "tool_input": {"prompt": "explore"}})
    assert result.exit_code == 2
    assert "lore tier resolve" in result.stderr


def test_allows_spawn_with_explicit_model() -> None:
    result = _invoke({"tool_name": "Task", "tool_input": {"model": "sonnet"}})
    assert result.exit_code == 0
    assert result.stderr == ""


def test_allows_fork_without_model() -> None:
    result = _invoke({"tool_name": "Task", "tool_input": {"subagent_type": "fork"}})
    assert result.exit_code == 0


def test_ignores_unrelated_tools() -> None:
    result = _invoke({"tool_name": "Bash", "tool_input": {"command": "ls"}})
    assert result.exit_code == 0


def test_fails_open_on_malformed_stdin() -> None:
    for garbage in ("", "not json", "[]"):
        result = _invoke(garbage)
        assert result.exit_code == 0, f"must fail open on {garbage!r}"
