"""`lore off [scope]` — mute Lore for the current session."""

from __future__ import annotations

import os
import sys

import typer

from lore_cli._argv_compat import argv_main
from lore_core.toggles import VALID_SCOPES, set_off

app = typer.Typer(
    add_completion=False,
    help=__doc__,
    no_args_is_help=False,
    rich_markup_mode="rich",
)


@app.callback(invoke_without_command=True)
def off(
    scope: str = typer.Argument(
        "all",
        help="Scope to mute. `all` mutes hooks + MCP + citations; `citations` mutes only the inline `› consulted` affordance.",
    ),
) -> None:
    """Write the per-session sentinel that hooks and MCP check before doing work."""
    if scope not in VALID_SCOPES:
        typer.echo(
            f"error: scope must be one of {VALID_SCOPES!r}, got {scope!r}", err=True
        )
        raise typer.Exit(code=2)
    sid = os.environ.get("CLAUDE_SESSION_ID")
    if not sid:
        typer.echo(
            "error: CLAUDE_SESSION_ID is not set — `lore off` is meaningful only inside a Claude Code session.",
            err=True,
        )
        raise typer.Exit(code=2)
    set_off(scope, sid)
    typer.echo(f"lore: muted ({scope}) for session {sid[:8]}…")


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
