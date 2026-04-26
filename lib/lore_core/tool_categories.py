"""Canonical tool-category classification.

Lore is glue between integrations (Claude Code, Cursor, VSCode Copilot,
future IDEs and agent frameworks). Each integration names its tools
differently: Claude Code's ``Edit`` is Cursor's ``edit_file`` is
Copilot's ``applyEdit``. Downstream consumers (noteworthy cascade,
Curator B surfaces, the knowledge graph) should not care which
integration a turn came from — they reason over
:data:`lore_core.types.ToolCategory`.

Each adapter calls :func:`classify_tool_name` when constructing a
ToolCall and stores the result in ``ToolCall.category``. Unknown or
novel tool names fall through to ``"other"`` — a neutral signal that
contributes nothing to the noteworthy score rather than silently being
miscategorised.

Adding a new integration: extend the mapping here, not downstream.
That guarantees feature extractors and surface generators pick the new
integration up for free.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lore_core.types import ToolCategory


# Integration-specific tool names → canonical category. Keyed by the raw
# ``ToolCall.name`` as emitted by each adapter. Case-sensitive to stay
# close to the wire format each integration uses.
_CLAUDE_CODE_MAP: dict[str, ToolCategory] = {
    "Edit": "file_edit",
    "MultiEdit": "file_edit",
    "Write": "file_edit",
    "NotebookEdit": "file_edit",
    "Read": "file_read",
    "NotebookRead": "file_read",
    "Grep": "search",
    "Glob": "search",
    "Bash": "shell_exec",
    "BashOutput": "shell_exec",
    "KillShell": "shell_exec",
    "Task": "agent_spawn",
    "ExitPlanMode": "plan_exit",
}

_CURSOR_MAP: dict[str, ToolCategory] = {
    "edit_file": "file_edit",
    "create_file": "file_edit",
    "write_file": "file_edit",
    "read_file": "file_read",
    "codebase_search": "search",
    "grep_search": "search",
    "file_search": "search",
    "run_terminal_cmd": "shell_exec",
    "run_in_terminal": "shell_exec",
}

_COPILOT_MAP: dict[str, ToolCategory] = {
    "applyEdit": "file_edit",
    "createFile": "file_edit",
    "readFile": "file_read",
    "workspaceSymbol": "search",
    "textSearch": "search",
    "executeCommand": "shell_exec",
}


def classify_tool_name(integration: str, name: str) -> ToolCategory:
    """Map an integration-specific tool name to its canonical category.

    Returns ``"other"`` for unknown names. Unknown-but-used tool names
    will show up in feature-vector logs; when a pattern becomes common,
    extend the appropriate per-integration map here.
    """
    if not name:
        return "other"
    if integration == "claude-code":
        return _CLAUDE_CODE_MAP.get(name, "other")
    if integration == "cursor":
        return _CURSOR_MAP.get(name, "other")
    if integration == "copilot":
        return _COPILOT_MAP.get(name, "other")
    return "other"
