"""`lore verdict` — list pending freshness verdicts and record one.

  lore verdict pending [--wiki W]            JSON: lore.pending_verdicts/1
  lore verdict set WIKI NOTE VERDICT [--reason R]
                                              JSON: the post-verdict freshness block

Both reuse the handlers in ``lore_mcp.server``; output is always JSON and
an error exits 1 with ``{"error": {...}}``. Backs `/lore:verify`.
"""

from __future__ import annotations

import json
import sys

import typer

from lore_cli._argv_compat import argv_main

app = typer.Typer(
    add_completion=False,
    help=__doc__,
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _emit(result: dict) -> None:
    print(json.dumps(result, indent=2, default=str))
    if "error" in result:
        raise typer.Exit(code=1)


@app.command("pending")
def cmd_pending(
    wiki: str = typer.Option(None, "--wiki", help="Wiki name; defaults to the cwd's route."),
) -> None:
    """List pending freshness verdicts as picker-ready rows (JSON)."""
    from lore_mcp.server import handle_pending_verdicts

    _emit(handle_pending_verdicts(wiki=wiki))


@app.command("set")
def cmd_set(
    wiki: str = typer.Argument(..., help="Wiki name."),
    note: str = typer.Argument(..., help="Note path, [[wikilink]] or bare slug."),
    verdict: str = typer.Argument(..., help="confirm | stale | clear-stale."),
    reason: str = typer.Option(None, "--reason", help="Required when verdict=stale."),
) -> None:
    """Record a freshness verdict for a note (JSON)."""
    from lore_mcp.server import handle_verdict

    _emit(handle_verdict(wiki=wiki, note=note, verdict=verdict, reason=reason))


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
