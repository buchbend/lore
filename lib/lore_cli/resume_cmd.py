"""`lore resume` — load working context from the vault.

Modes (one per invocation):
  - no args              recent sessions across all wikis
  - --wiki <name>        recent sessions in one wiki
  - --keyword <term>     ranked keyword search via FTS5
  - --scope <prefix>     aggregate gh issues + PRs + session notes for a
                         scope subtree (e.g. `ccat:data-center`)

Used by:
  - the `/lore:resume` skill (display-layer wrapper over MCP `lore_resume`)
  - any user wanting a shell-side context dump or pre-warmed launcher
"""

from __future__ import annotations

import json
import sys

import typer

from lore_cli._compat import argv_main
from lore_cli.launcher import launch, list_hosts
from lore_core.resume import (
    DEFAULT_DAYS,
    DEFAULT_ISSUES_FILTER,
    DEFAULT_KEYWORD_K,
    DEFAULT_PRS_FILTER,
    format_markdown,
    gather,
)

app = typer.Typer(
    add_completion=False,
    help=__doc__,
    no_args_is_help=False,
    rich_markup_mode="rich",
)


@app.callback(invoke_without_command=True)
def resume(
    scope: str = typer.Option(
        None, "--scope", help="Scope prefix to aggregate (e.g. ccat:data-center)."
    ),
    keyword: str = typer.Option(
        None, "--keyword", help="Keyword for ranked FTS5 search across the vault."
    ),
    wiki: str = typer.Option(
        None,
        "--wiki",
        help="Restrict to one wiki (default: all wikis for recent mode, "
        "auto-detect for scope mode).",
    ),
    days: int = typer.Option(
        DEFAULT_DAYS,
        "--days",
        help=f"Recency window for sessions (default {DEFAULT_DAYS}d, recent mode only).",
    ),
    k: int = typer.Option(
        DEFAULT_KEYWORD_K,
        "--k",
        help=f"Top-k results for keyword search (default {DEFAULT_KEYWORD_K}).",
    ),
    issues_filter: str = typer.Option(
        DEFAULT_ISSUES_FILTER,
        "--issues",
        help=f"gh issue list filter flags (scope mode; default: {DEFAULT_ISSUES_FILTER!r}).",
    ),
    prs_filter: str = typer.Option(
        DEFAULT_PRS_FILTER,
        "--prs",
        help=f"gh pr list filter flags (scope mode; default: {DEFAULT_PRS_FILTER!r}).",
    ),
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit JSON envelope on stdout instead of markdown.",
    ),
    launch_host: str = typer.Option(
        None,
        "--launch",
        metavar="HOST",
        help=(
            "Gather context, then exec the named agent host pre-warmed "
            f"(known: {', '.join(list_hosts()) or 'none'})."
        ),
    ),
    launch_dry_run: bool = typer.Option(
        False,
        "--launch-dry-run",
        help="With --launch: print the would-be invocation, do not exec.",
    ),
    launch_message: str = typer.Option(
        None,
        "--launch-message",
        help="Initial user message to pass to the launched host (optional).",
    ),
) -> None:
    """Load working context from the vault."""
    result = gather(
        scope=scope,
        wiki=wiki,
        keyword=keyword,
        days=days,
        k=k,
        issues_filter=issues_filter,
        prs_filter=prs_filter,
    )

    if launch_host:
        if "error" in result and not result.get("mode"):
            print(f"lore: {result['error']}", file=sys.stderr)
            raise typer.Exit(code=1)
        context_text = format_markdown(result)
        rc = launch(
            launch_host,
            context_text=context_text,
            user_message=launch_message,
            dry_run=launch_dry_run,
        )
        raise typer.Exit(code=rc)

    if json_out:
        print(json.dumps({"schema": "lore.resume/1", "data": result}, indent=2))
    else:
        print(format_markdown(result))

    if "error" in result and not result.get("mode"):
        raise typer.Exit(code=1)


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
