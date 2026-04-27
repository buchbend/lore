"""`lore mcp` — start the MCP STDIO server."""

from __future__ import annotations

import sys

import typer
from lore_mcp.server import start_server

from lore_cli._argv_compat import argv_main

app = typer.Typer(
    add_completion=False,
    help="MCP server exposing vault retrieval to any MCP client.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)


@app.callback(invoke_without_command=True)
def mcp() -> None:
    """Start the Lore MCP STDIO server.

    Communicates over stdin/stdout. Intended for invocation by an MCP
    client (Claude Code, Cursor, etc.) — running interactively will
    just wait for JSON-RPC messages.
    """
    rc = start_server()
    if rc:
        raise typer.Exit(code=rc)


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
