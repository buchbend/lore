"""Pure deny/allow logic for the spawn-model gate (lore_core.spawn_gate).

Ported from ccat-agent-workflow's test_spawn_model_hook.py. The CLI/stdin
plumbing (exit codes, stderr) is covered separately in
test_hooks_spawn_model_gate.py; this file only exercises check_spawn().
"""

from __future__ import annotations

from lore_core.spawn_gate import check_spawn


def test_blocks_task_spawn_without_model() -> None:
    msg = check_spawn({"tool_name": "Task", "tool_input": {"prompt": "explore"}})
    assert msg is not None


def test_blocks_agent_spawn_without_model() -> None:
    msg = check_spawn({"tool_name": "Agent", "tool_input": {"prompt": "explore"}})
    assert msg is not None


def test_blocks_blank_model() -> None:
    msg = check_spawn({"tool_name": "Task", "tool_input": {"model": "  "}})
    assert msg is not None


def test_deny_message_points_at_tier_resolver() -> None:
    msg = check_spawn({"tool_name": "Task", "tool_input": {"prompt": "explore"}})
    assert msg is not None
    assert "lore tier resolve" in msg
    assert "model" in msg.lower()


def test_allows_spawn_with_explicit_model() -> None:
    assert check_spawn({"tool_name": "Task", "tool_input": {"model": "sonnet"}}) is None


def test_allows_fork_without_model() -> None:
    """Forks always inherit the parent model by design; the gate exempts them."""
    assert check_spawn({"tool_name": "Task", "tool_input": {"subagent_type": "fork"}}) is None


def test_ignores_unrelated_tools() -> None:
    assert check_spawn({"tool_name": "Bash", "tool_input": {"command": "ls"}}) is None


def test_ignores_near_miss_tool_names() -> None:
    """Task-management tools (TaskCreate, TaskStop, ...) are not spawns."""
    for name in ("TaskCreate", "TaskStop", "TaskOutput", "AgentOutput"):
        assert check_spawn({"tool_name": name, "tool_input": {}}) is None


def test_fails_open_on_malformed_payload() -> None:
    """A broken/unexpected payload shape must never brick delegation."""
    for garbage in (None, [], 42, "not a dict", {"tool_name": 42}):
        assert check_spawn(garbage) is None


def test_opencode_subagent_without_a_pinned_model_is_denied():
    msg = check_spawn({"tool_name": "subagent", "tool_input": {"agent": "general", "model": ""}})
    assert msg and "lore tier resolve" in msg and "agent" in msg


def test_opencode_subagent_with_a_pinned_model_is_allowed():
    payload = {"tool_name": "subagent", "tool_input": {"agent": "lore-mid", "model": "a/b"}}
    assert check_spawn(payload) is None


def test_opencode_unknown_agent_is_named_not_called_pinless():
    payload = {
        "tool_name": "subagent",
        "tool_input": {"agent": "ghost", "model": "", "agent_known": False},
    }
    msg = check_spawn(payload)
    assert msg and "ghost" in msg and "does not know" in msg and "pins no model" not in msg
