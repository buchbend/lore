"""`lore off [scope]` / `lore on [scope]` — mute and un-mute Lore.

One toggle, two verbs: both write the same per-session sentinel that
hooks and MCP check before doing work, so they live in one module over
a shared implementation rather than drifting apart in two.

The sentinel is session-scoped — muting is a property of the session
you're in, not of the vault — so a missing ``CLAUDE_SESSION_ID`` is a
hard error rather than a silent global mute.
"""

from __future__ import annotations

import os
import sys

import typer
from lore_core.toggles import VALID_SCOPES, clear_off, set_off

from lore_cli._argv_compat import argv_main


def _session_id_or_die(verb: str) -> str:
    sid = os.environ.get("CLAUDE_SESSION_ID")
    if not sid:
        typer.echo(
            f"error: CLAUDE_SESSION_ID is not set — `lore {verb}` is meaningful "
            "only inside a Claude Code session.",
            err=True,
        )
        raise typer.Exit(code=2)
    return sid


def _toggle(scope: str, *, verb: str, mute: bool) -> None:
    if scope not in VALID_SCOPES:
        typer.echo(
            f"error: scope must be one of {VALID_SCOPES!r}, got {scope!r}", err=True
        )
        raise typer.Exit(code=2)
    sid = _session_id_or_die(verb)
    (set_off if mute else clear_off)(scope, sid)
    state = "muted" if mute else "un-muted"
    typer.echo(f"lore: {state} ({scope}) for session {sid[:8]}…")


off_app = typer.Typer(
    add_completion=False,
    help="Mute Lore for the current session.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)

on_app = typer.Typer(
    add_completion=False,
    help="Re-enable Lore for the current session.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)


@off_app.callback(invoke_without_command=True)
def off(
    scope: str = typer.Argument(
        "all",
        help="Scope to mute. `all` mutes hooks + MCP + citations; `citations` "
             "mutes only the inline `› consulted` affordance.",
    ),
) -> None:
    """Write the per-session sentinel that hooks and MCP check before doing work."""
    _toggle(scope, verb="off", mute=True)


@on_app.callback(invoke_without_command=True)
def on(
    scope: str = typer.Argument(
        "all",
        help="Scope to un-mute. Inverse of `lore off <scope>`.",
    ),
) -> None:
    """Remove the per-session sentinel so hooks and MCP resume immediately."""
    _toggle(scope, verb="on", mute=False)


main = argv_main(off_app)


if __name__ == "__main__":
    sys.exit(main())
