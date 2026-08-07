"""Structured error envelope shared by the MCP server and handler modules.

Tool handlers return ``{"error": {"code", "message", "next"}}`` so MCP
clients (Claude, Cursor, …) can branch on ``code`` for retry logic
instead of pattern-matching English strings. ``next`` is an optional
recovery hint shown verbatim to the user.

This module lives in ``lore_core`` (not ``lore_mcp``) because handler
modules under ``lore_core`` build error envelopes for MCP consumption
(``resume.gather``, ``inbox.classify``, ``briefing.gather``, etc.) — if
the helper lived in ``lore_mcp``, ``lore_core`` would import "up" into
the MCP layer. The ``lore_mcp`` server re-exports the helper for
back-compat with its private ``_mcp_error`` name.

The JSON-RPC protocol-level error responses (``-32xxx`` codes) at the
MCP dispatcher use the JSON-RPC standard shape and are *not* this
envelope — different layer, different contract.
"""

from __future__ import annotations

from typing import Any

# Canonical error codes — keep client-facing strings centralised so the
# MCP tool docs and handler modules stay in sync. Every emit site imports
# its constant; a code written as a literal drifts from the docs silently,
# because renaming the constant then breaks nothing.
NO_VAULT = "no_vault"
NO_WIKIS = "no_wikis"
WIKI_NOT_FOUND = "wiki_not_found"
SOURCE_NOT_FOUND = "source_not_found"
SOURCE_NOT_A_FILE = "source_not_a_file"
NOTE_NOT_FOUND = "note_not_found"
PATH_NOT_FOUND = "path_not_found"
PATH_ESCAPE = "path_escape"
SESSION_OFF = "session_off"
UNKNOWN_TOOL = "unknown_tool"
INVALID_KIND = "invalid_kind"
INVALID_ENTRY = "invalid_entry"
EMPTY_ENTRY = "empty_entry"


def mcp_error(
    code: str, message: str, *, next_: str | None = None
) -> dict[str, Any]:
    """Build the canonical MCP error envelope."""
    payload: dict[str, Any] = {"code": code, "message": message}
    if next_:
        payload["next"] = next_
    return {"error": payload}
