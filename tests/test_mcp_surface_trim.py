"""MCP surface trim — six no-caller tools removed from exposure.

`lore_index`, `lore_catalog`, `lore_wikilinks`, `lore_journal_read`, and
`lore_briefing_gather` have no caller (no skill, template, integration-rules
file, or hook instructs a model to call them). The underlying core they
wrapped stays reachable through other paths: `lore_core/wikilinks.py` backs
`lore_drill`'s expand stage, `lore briefing gather` is a CLI command, and
`lore_core.journal` is still written via `lore_journal_write`.

`lore_resume` joins the removed set on its own count: telemetry over 307
sessions recorded zero calls (ADR 0007, issue #359). Its gather logic that
`lore_context_pack` still depends on moved into `lore_core.context_pack`.
"""

from __future__ import annotations

from lore_mcp.server import _dispatch, _tool_schema

REMOVED_TOOLS = {
    "lore_index",
    "lore_catalog",
    "lore_wikilinks",
    "lore_journal_read",
    "lore_briefing_gather",
    "lore_resume",
}


def test_tool_schema_has_exactly_twelve_tools() -> None:
    names = [t["name"] for t in _tool_schema()]
    assert len(names) == 12, names


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
        "handle_resume",
    ):
        assert not hasattr(server, handler), handler
