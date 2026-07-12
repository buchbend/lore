"""MCP surface trim — five no-caller tools removed from exposure.

`lore_index`, `lore_catalog`, `lore_wikilinks`, `lore_journal_read`, and
`lore_briefing_gather` have no caller (no skill, template, integration-rules
file, or hook instructs a model to call them). The underlying core they
wrapped stays reachable through other paths: `lore_core/wikilinks.py` backs
`lore_drill`'s expand stage, `lore briefing gather` is a CLI command, and
`lore_core.journal` is still written via `lore_journal_write`.
"""
from __future__ import annotations

from lore_mcp.server import _dispatch, _tool_schema

REMOVED_TOOLS = {
    "lore_index",
    "lore_catalog",
    "lore_wikilinks",
    "lore_journal_read",
    "lore_briefing_gather",
}


def test_tool_schema_has_exactly_thirteen_tools() -> None:
    names = [t["name"] for t in _tool_schema()]
    assert len(names) == 13, names


def test_tool_schema_excludes_removed_tools() -> None:
    names = {t["name"] for t in _tool_schema()}
    assert names.isdisjoint(REMOVED_TOOLS), names & REMOVED_TOOLS


def test_dispatch_rejects_removed_tool_names() -> None:
    for name in REMOVED_TOOLS:
        result = _dispatch(name, {})
        assert result["error"]["code"] == "unknown_tool", (name, result)


def test_removed_handlers_no_longer_defined() -> None:
    import lore_mcp.server as server

    for handler in (
        "handle_index",
        "handle_catalog",
        "handle_wikilinks",
        "handle_journal_read",
        "handle_briefing_gather",
    ):
        assert not hasattr(server, handler), handler
