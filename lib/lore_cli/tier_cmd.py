"""`lore tier resolve <tier>` — resolve a semantic model tier for this host.

    lore tier resolve mid
    lore tier resolve frontier --host cursor

See ``docs/model-tiers.md`` and :mod:`lore_core.tiers`.
"""

from __future__ import annotations

import sys

import typer
from lore_core.tiers import TierResolutionError, resolve_tier
from rich.console import Console

from lore_cli._argv_compat import argv_main

console = Console()

app = typer.Typer(
    add_completion=False,
    help=__doc__,
    no_args_is_help=True,
    rich_markup_mode="rich",
)


@app.command("resolve")
def resolve(
    tier: str = typer.Argument(..., help="Semantic tier: frontier | strong | mid | cheap."),
    host: str = typer.Option(None, "--host", help="Override host detection (e.g. cursor)."),
) -> None:
    """Print the concrete model resolved for TIER on the current (or given) host."""
    try:
        console.print(resolve_tier(tier, host=host))
    except TierResolutionError as e:
        console.print(f"[red]error:[/red] {e}")
        raise typer.Exit(code=1) from None


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
