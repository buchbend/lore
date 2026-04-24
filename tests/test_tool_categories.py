"""Tests for lore_core.tool_categories — host-agnostic tool classification.

Lore is glue between hosts (Claude Code, Cursor, VSCode Copilot, …).
Each host names its tools differently. Downstream consumers reason over
canonical :data:`ToolCategory` values so they don't need to know that
Claude Code's ``Edit`` is Cursor's ``edit_file``.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Claude Code — the host we have the most history with
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,expected", [
    ("Edit", "file_edit"),
    ("MultiEdit", "file_edit"),
    ("Write", "file_edit"),
    ("NotebookEdit", "file_edit"),
    ("Read", "file_read"),
    ("NotebookRead", "file_read"),
    ("Grep", "search"),
    ("Glob", "search"),
    ("Bash", "shell_exec"),
    ("Task", "agent_spawn"),
    ("ExitPlanMode", "plan_exit"),
])
def test_claude_code_names_map_to_canonical_categories(name, expected):
    from lore_core.tool_categories import classify_tool_name

    assert classify_tool_name("claude-code", name) == expected


def test_claude_code_unknown_name_is_other():
    """Novel / mis-cased / future tool names must fall through to 'other' —
    a neutral signal that contributes nothing to the noteworthy score
    rather than silently being miscategorised."""
    from lore_core.tool_categories import classify_tool_name

    assert classify_tool_name("claude-code", "SomeFutureTool") == "other"
    assert classify_tool_name("claude-code", "edit") == "other"  # case-sensitive


# ---------------------------------------------------------------------------
# Cursor — different tool vocabulary, same semantics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,expected", [
    ("edit_file", "file_edit"),
    ("create_file", "file_edit"),
    ("read_file", "file_read"),
    ("codebase_search", "search"),
    ("grep_search", "search"),
    ("run_terminal_cmd", "shell_exec"),
])
def test_cursor_names_map_to_canonical_categories(name, expected):
    from lore_core.tool_categories import classify_tool_name

    assert classify_tool_name("cursor", name) == expected


# ---------------------------------------------------------------------------
# VSCode Copilot
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,expected", [
    ("applyEdit", "file_edit"),
    ("readFile", "file_read"),
    ("workspaceSymbol", "search"),
    ("executeCommand", "shell_exec"),
])
def test_copilot_names_map_to_canonical_categories(name, expected):
    from lore_core.tool_categories import classify_tool_name

    assert classify_tool_name("copilot", name) == expected


# ---------------------------------------------------------------------------
# Fallthrough & edge cases
# ---------------------------------------------------------------------------


def test_unknown_host_returns_other():
    """An adapter for a new host that hasn't been added to the map should
    not silently miscategorise its tools — everything falls to 'other'
    until the map is extended."""
    from lore_core.tool_categories import classify_tool_name

    assert classify_tool_name("future-host", "AnyTool") == "other"


def test_empty_name_returns_other():
    from lore_core.tool_categories import classify_tool_name

    assert classify_tool_name("claude-code", "") == "other"


def test_cross_host_same_category_symmetry():
    """The whole point of this module: a 'file edit' on any host is a
    'file_edit' canonically. Verify the vocabularies agree."""
    from lore_core.tool_categories import classify_tool_name

    assert classify_tool_name("claude-code", "Edit") == "file_edit"
    assert classify_tool_name("cursor", "edit_file") == "file_edit"
    assert classify_tool_name("copilot", "applyEdit") == "file_edit"
