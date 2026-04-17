"""Lore MCP server — exposes vault retrieval over the Model Context Protocol.

Runs as a local STDIO server. Any MCP client (Claude Desktop, Cursor,
Windsurf, Zed, etc.) can register this and query the vault.

Exposed tools:
    lore_search      — hybrid ranked search, top-k paths
    lore_read        — read one note by wiki/path
    lore_index       — return a wiki's _index.md
    lore_catalog     — return a wiki's _catalog.json
    lore_resume      — sessions + open items bundle
    lore_wikilinks   — in/out wikilinks for a note

Start:
    lore mcp
(or)
    python -m lore_mcp.server
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from lore_core.config import get_wiki_root
from lore_core.schema import extract_wikilinks, parse_frontmatter
from lore_search.fts import FtsBackend

# ---------------------------------------------------------------------------
# Handlers (pure Python, usable by the MCP wrapper or a test harness)
# ---------------------------------------------------------------------------


def _resolve_wiki(wiki: str | None) -> Path | None:
    """Resolve a wiki name to its on-disk path."""
    wiki_root = get_wiki_root()
    if not wiki_root.exists():
        return None
    if wiki:
        target = wiki_root / wiki
        return target if target.resolve().is_dir() else None
    # Single-wiki users: return the only one
    wikis = [p for p in sorted(wiki_root.iterdir()) if p.resolve().is_dir()]
    return wikis[0] if len(wikis) == 1 else None


def handle_search(
    query: str,
    wiki: str | None = None,
    for_repo: str | None = None,
    k: int = 5,
) -> list[dict[str, Any]]:
    backend = FtsBackend()
    backend.reindex(wiki=wiki)
    hits = backend.search(query, wiki=wiki, for_repo=for_repo, k=k)
    return [
        {
            "path": h.path,
            "wiki": h.wiki,
            "filename": h.filename,
            "score": round(h.score, 3),
            "description": h.description,
            "tags": h.tags or [],
        }
        for h in hits
    ]


def handle_read(path: str, wiki: str | None = None) -> dict[str, Any]:
    wiki_path = _resolve_wiki(wiki)
    if wiki_path is None:
        return {"error": f"wiki not found: {wiki}"}
    target = (wiki_path / path).resolve()
    try:
        target.relative_to(wiki_path.resolve())
    except ValueError:
        return {"error": "path escapes wiki root"}
    if not target.exists():
        return {"error": f"not found: {path}"}
    text = target.read_text(errors="replace")
    return {
        "wiki": wiki_path.name,
        "path": path,
        "content": text,
    }


def handle_index(wiki: str | None = None) -> dict[str, Any]:
    wiki_path = _resolve_wiki(wiki)
    if wiki_path is None:
        return {"error": f"wiki not found: {wiki}"}
    index = wiki_path / "_index.md"
    if not index.exists():
        return {"error": "no _index.md — run `lore lint` first"}
    return {"wiki": wiki_path.name, "content": index.read_text(errors="replace")}


def handle_catalog(wiki: str | None = None) -> dict[str, Any]:
    wiki_path = _resolve_wiki(wiki)
    if wiki_path is None:
        return {"error": f"wiki not found: {wiki}"}
    cat = wiki_path / "_catalog.json"
    if not cat.exists():
        return {"error": "no _catalog.json — run `lore lint` first"}
    return json.loads(cat.read_text())


def handle_resume(wiki: str | None = None, days: int = 7) -> dict[str, Any]:
    wiki_path = _resolve_wiki(wiki)
    if wiki_path is None:
        return {"error": f"wiki not found: {wiki}"}
    sessions_dir = wiki_path / "sessions"
    if not sessions_dir.exists():
        return {"wiki": wiki_path.name, "sessions": [], "open_items": []}

    cutoff = date.today() - timedelta(days=days)
    sessions: list[dict] = []
    open_items: list[str] = []
    seen: set[str] = set()

    for md in sorted(sessions_dir.glob("*.md"), reverse=True):
        try:
            iso = md.stem[:10]
            d = date.fromisoformat(iso)
        except (ValueError, IndexError):
            continue
        if d < cutoff:
            continue
        text = md.read_text(errors="replace")
        fm = parse_frontmatter(text)
        sessions.append(
            {
                "path": str(md.relative_to(wiki_path)),
                "date": iso,
                "title": md.stem,
                "description": fm.get("description"),
            }
        )
        # Parse ## Open items
        import re

        m = re.search(r"##\s+Open items\s*\n(.+?)(?=\n##|\Z)", text, re.DOTALL)
        if not m:
            continue
        for line in m.group(1).splitlines():
            line = line.strip()
            if not line.startswith("-"):
                continue
            body = line.lstrip("-").strip()
            if not body or body.lower() == "none" or body in seen:
                continue
            seen.add(body)
            open_items.append(body)

    return {
        "wiki": wiki_path.name,
        "sessions": sessions[:10],
        "open_items": open_items[:20],
    }


def handle_wikilinks(note: str, wiki: str | None = None) -> dict[str, Any]:
    wiki_path = _resolve_wiki(wiki)
    if wiki_path is None:
        return {"error": f"wiki not found: {wiki}"}
    cat_path = wiki_path / "_catalog.json"
    if not cat_path.exists():
        return {"error": "no _catalog.json — run `lore lint` first"}
    catalog = json.loads(cat_path.read_text())
    for entries in catalog.get("sections", {}).values():
        for entry in entries:
            if entry["name"] == note or entry["path"] == note:
                return {
                    "wiki": wiki_path.name,
                    "note": entry["name"],
                    "links_out": entry.get("links_out", []),
                    "links_in": entry.get("links_in", []),
                }
    # Fall back to live parse
    candidates = list(wiki_path.rglob(f"{note}.md"))
    if candidates:
        text = candidates[0].read_text(errors="replace")
        return {
            "wiki": wiki_path.name,
            "note": note,
            "links_out": extract_wikilinks(text),
            "links_in": [],
            "note_missing_from_catalog": True,
        }
    return {"error": f"note not found: {note}"}


# ---------------------------------------------------------------------------
# MCP server wrapper
# ---------------------------------------------------------------------------


def _tool_schema() -> list[dict]:
    return [
        {
            "name": "lore_search",
            "description": (
                "Hybrid ranked search across the vault's knowledge notes. "
                "Returns top-k paths with descriptions and scores."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "wiki": {"type": "string", "description": "Scope to one wiki (optional)"},
                    "for_repo": {
                        "type": "string",
                        "description": "Boost notes tagged with this repo (org/name)",
                    },
                    "k": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        },
        {
            "name": "lore_read",
            "description": "Read one note by relative path within a wiki.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "wiki": {"type": "string"},
                },
                "required": ["path"],
            },
        },
        {
            "name": "lore_index",
            "description": "Return the wiki's _index.md (human/LLM-scannable knowledge map).",
            "inputSchema": {
                "type": "object",
                "properties": {"wiki": {"type": "string"}},
            },
        },
        {
            "name": "lore_catalog",
            "description": "Return the wiki's _catalog.json (full machine-readable metadata + link graph).",
            "inputSchema": {
                "type": "object",
                "properties": {"wiki": {"type": "string"}},
            },
        },
        {
            "name": "lore_resume",
            "description": (
                "Return recent sessions and unresolved open items for a wiki. "
                "Use at session start to load working context."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "wiki": {"type": "string"},
                    "days": {"type": "integer", "default": 7},
                },
            },
        },
        {
            "name": "lore_wikilinks",
            "description": "Return incoming and outgoing [[wikilinks]] for a note (graph traversal).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "note": {"type": "string"},
                    "wiki": {"type": "string"},
                },
                "required": ["note"],
            },
        },
    ]


def _dispatch(tool_name: str, args: dict) -> Any:
    match tool_name:
        case "lore_search":
            return handle_search(**args)
        case "lore_read":
            return handle_read(**args)
        case "lore_index":
            return handle_index(**args)
        case "lore_catalog":
            return handle_catalog(**args)
        case "lore_resume":
            return handle_resume(**args)
        case "lore_wikilinks":
            return handle_wikilinks(**args)
        case _:
            return {"error": f"unknown tool: {tool_name}"}


def main(argv: list[str] | None = None) -> int:
    """Start the MCP STDIO server.

    Uses the official `mcp` Python SDK if installed; otherwise falls back
    to a minimal JSON-RPC-over-stdio loop that's compatible with the
    MCP core protocol for tool listing and invocation.
    """
    try:
        from mcp.server import Server  # type: ignore[import-untyped]
        from mcp.server.stdio import stdio_server  # type: ignore[import-untyped]
        from mcp.types import TextContent, Tool  # type: ignore[import-untyped]
    except ImportError:
        return _run_minimal_server()

    import asyncio

    server = Server("lore")
    schema = _tool_schema()

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return [
            Tool(
                name=s["name"],
                description=s["description"],
                inputSchema=s["inputSchema"],
            )
            for s in schema
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict) -> list[TextContent]:
        result = _dispatch(name, arguments or {})
        return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

    async def _run() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(_run())
    return 0


def _run_minimal_server() -> int:
    """Fallback minimal JSON-RPC STDIO loop when mcp SDK isn't installed.

    Handles enough of the MCP surface (`initialize`, `tools/list`,
    `tools/call`) to be useful for testing. Production use should pip
    install `mcp`.
    """
    sys.stderr.write(
        "lore_mcp: `mcp` package not installed; running minimal fallback.\n"
        "Install with: pip install 'lore[mcp]' or pip install mcp\n"
    )
    schema = _tool_schema()

    def _send(obj: dict) -> None:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        rid = req.get("id")
        method = req.get("method")
        params = req.get("params") or {}

        if method == "initialize":
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "lore", "version": "0.1.0"},
                    },
                }
            )
        elif method == "tools/list":
            _send({"jsonrpc": "2.0", "id": rid, "result": {"tools": schema}})
        elif method == "tools/call":
            name = params.get("name", "")
            args = params.get("arguments", {}) or {}
            try:
                result = _dispatch(name, args)
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": rid,
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": json.dumps(result, indent=2, default=str),
                                }
                            ]
                        },
                    }
                )
            except Exception as exc:  # noqa: BLE001
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": rid,
                        "error": {"code": -32603, "message": str(exc)},
                    }
                )
        else:
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "error": {"code": -32601, "message": f"method not found: {method}"},
                }
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
