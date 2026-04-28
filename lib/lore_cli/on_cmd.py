"""`lore on [scope]` — re-enable Lore for the current session."""

from __future__ import annotations

import os
import sys

import typer

from lore_cli._argv_compat import argv_main
from lore_core.toggles import VALID_SCOPES, clear_off

app = typer.Typer(
    add_completion=False,
    help=__doc__,
    no_args_is_help=False,
    rich_markup_mode="rich",
)


@app.callback(invoke_without_command=True)
def on(
    scope: str = typer.Argument(
        "all",
        help="Scope to un-mute. Inverse of `lore off <scope>`.",
    ),
) -> None:
    """Remove the per-session sentinel so hooks and MCP resume immediately."""
    if scope not in VALID_SCOPES:
        typer.echo(
            f"error: scope must be one of {VALID_SCOPES!r}, got {scope!r}", err=True
        )
        raise typer.Exit(code=2)
    sid = os.environ.get("CLAUDE_SESSION_ID")
    if not sid:
        typer.echo(
            "error: CLAUDE_SESSION_ID is not set — `lore on` is meaningful only inside a Claude Code session.",
            err=True,
        )
        raise typer.Exit(code=2)
    clear_off(scope, sid)
    typer.echo(f"lore: un-muted ({scope}) for session {sid[:8]}…")


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
