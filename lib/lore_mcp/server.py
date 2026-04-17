"""Lore MCP server — exposes vault retrieval over the Model Context Protocol.

Runs as a local STDIO server. Any MCP client (Claude Desktop, Cursor,
Windsurf, Zed, etc.) can register this and query the vault.

Exposed tools:
    lore_search             — hybrid ranked search, top-k paths
    lore_read               — read one note by wiki/path
    lore_index              — return a wiki's _index.md
    lore_catalog            — return a wiki's _catalog.json
    lore_resume             — unified context gather (recent/wiki/keyword/scope)
    lore_wikilinks          — in/out wikilinks for a note
    lore_session_scaffold   — read-only scaffold (path + frontmatter) for a new
                              session note; the subagent uses this before any
                              write so the deterministic work happens once
    lore_briefing_gather    — read-only briefing gather (new sessions since last
                              briefing + sink config + ledger); skill writes
                              prose, then shells out to publish + mark
    lore_inbox_classify     — read-only inbox walk (file list with type +
                              routing hint); skill composes notes, then shells
                              out to `lore inbox archive`

Start:
    lore mcp
(or)
    python -m lore_mcp.server
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from lore_core.config import get_wiki_root
from lore_core.schema import extract_wikilinks
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


def handle_resume(
    wiki: str | None = None,
    days: int = 3,
    keyword: str | None = None,
    scope: str | None = None,
    k: int = 5,
) -> dict[str, Any]:
    """Unified resume gather. Delegates to lore_core.resume.gather().

    Modes (priority): scope > keyword > recent (wiki-scoped or all wikis).
    """
    from lore_core.resume import gather

    return gather(
        scope=scope,
        wiki=wiki,
        keyword=keyword,
        days=days,
        k=k,
    )


def handle_briefing_gather(
    wiki: str,
    since: str | None = None,
    include_body_sections: bool = True,
) -> dict[str, Any]:
    """Read-only briefing gather. Delegates to lore_core.briefing.gather()."""
    from lore_core.briefing import gather

    return gather(
        wiki=wiki, since=since, include_body_sections=include_body_sections
    )


def handle_inbox_classify() -> dict[str, Any]:
    """Read-only inbox classifier. Delegates to lore_core.inbox.classify()."""
    from lore_core.inbox import classify

    return classify()


def handle_session_scaffold(
    cwd: str,
    slug: str,
    description: str,
    title: str | None = None,
    target_wiki: str | None = None,
    extra_repos: list[str] | None = None,
    tags: list[str] | None = None,
    implements: list[str] | None = None,
    loose_ends: list[str] | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    """Read-only scaffold for a new session note.

    Returns the computed path, frontmatter, body template, and identity
    state — pure data, no file write. The caller (subagent) then composes
    the prose body and writes via the CLI subprocess `lore session new`.
    """
    from lore_core.session import scaffold

    return scaffold(
        cwd=cwd,
        slug=slug,
        description=description,
        title=title,
        target_wiki=target_wiki,
        extra_repos=extra_repos,
        tags=tags,
        implements=implements,
        loose_ends=loose_ends,
        project=project,
    )


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
                "Load working context from the vault. Modes (priority "
                "order): scope > keyword > recent. Returns a structured "
                "dict with `mode` discriminator. Use at session start or "
                "any time the agent needs broader context without "
                "iterating through Glob/Read."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "description": "Scope prefix to aggregate gh issues + PRs + sessions for (e.g. ccat:data-center)",
                    },
                    "keyword": {
                        "type": "string",
                        "description": "FTS5 ranked search across the vault",
                    },
                    "wiki": {
                        "type": "string",
                        "description": "Restrict to one wiki (default: all wikis for recent mode)",
                    },
                    "days": {
                        "type": "integer",
                        "default": 3,
                        "description": "Recency window for sessions (recent mode only)",
                    },
                    "k": {
                        "type": "integer",
                        "default": 5,
                        "description": "Top-k results for keyword search",
                    },
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
        {
            "name": "lore_briefing_gather",
            "description": (
                "Read-only briefing gather: returns the new session "
                "notes (since the last briefing) plus the wiki's sink "
                "config and ledger state. Caller composes the briefing "
                "prose, then shells out to `lore briefing publish` and "
                "`lore briefing mark`. No LLM call inside the tool."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "wiki": {"type": "string"},
                    "since": {
                        "type": "string",
                        "description": "ISO date floor (YYYY-MM-DD)",
                    },
                    "include_body_sections": {
                        "type": "boolean",
                        "default": True,
                        "description": "Extract H2 sections per session",
                    },
                },
                "required": ["wiki"],
            },
        },
        {
            "name": "lore_inbox_classify",
            "description": (
                "Read-only inbox walk: returns every file in the root "
                "inbox and per-wiki inboxes with detected type and "
                "routing hint. Caller reads each file, composes vault "
                "notes (LLM judgment), then runs `lore inbox archive` "
                "to move the source to `.processed/`."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "lore_session_scaffold",
            "description": (
                "Compute path, frontmatter, identity, and recent-commits "
                "for a new session note — read-only, no file write. Call "
                "this BEFORE composing the session body so the determinist "
                "work (routing, scope, handle, sharded path, frontmatter) "
                "happens once. Then write the file via the CLI subprocess "
                "`lore session new --body -` < <body>."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "cwd": {
                        "type": "string",
                        "description": "Working directory the session ran in",
                    },
                    "slug": {
                        "type": "string",
                        "description": "Short kebab-case topic identifier",
                    },
                    "description": {
                        "type": "string",
                        "description": "One-sentence summary",
                    },
                    "title": {"type": "string", "description": "Note H1 (default: slug)"},
                    "target_wiki": {
                        "type": "string",
                        "description": "Wiki name (default: from `## Lore` block or only-wiki)",
                    },
                    "extra_repos": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Additional repos to tag beyond the cwd's repo",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Frontmatter tags (3–5 max)",
                    },
                    "implements": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Proposal slugs that landed in this session",
                    },
                    "loose_ends": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Frontmatter loose-end strings",
                    },
                    "project": {"type": "string", "description": "Primary project name"},
                },
                "required": ["cwd", "slug", "description"],
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
        case "lore_session_scaffold":
            return handle_session_scaffold(**args)
        case "lore_briefing_gather":
            return handle_briefing_gather(**args)
        case "lore_inbox_classify":
            return handle_inbox_classify(**args)
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
