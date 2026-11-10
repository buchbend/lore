"""MCP server lists seven tools within a schema budget (PRD 0020, #552)."""

from __future__ import annotations

import json

from lore_mcp.server import _tool_schema

SEVEN = {
    "lore_search",
    "lore_read",
    "lore_drill",
    "lore_context_pack",
    "lore_repo_docs_list",
    "lore_repo_docs_fetch",
    "lore_codemap",
}


def test_seven_tools_within_schema_budget() -> None:
    names = [t["name"] for t in _tool_schema()]
    assert set(names) == SEVEN and len(names) == 7
    assert len(json.dumps(_tool_schema())) < 6000
