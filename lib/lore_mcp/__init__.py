"""lore_mcp — MCP server exposing vault retrieval to any MCP client.

Wraps `lore_search` and the linter's `_catalog.json` / `_index.md`
outputs behind the MCP tool surface:
lore_search, lore_read, lore_index, lore_catalog, lore_resume, lore_wikilinks.
"""

from lore_mcp.server import main

__all__ = ["main"]

