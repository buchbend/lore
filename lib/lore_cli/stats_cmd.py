"""`lore stats` — write the token-statistics page and open it in the browser.

    lore stats
    lore stats --days 7
    lore stats --no-open

The page sums the token records that the SessionEnd hook writes: cost per
day, by model, by workflow stage, by epic or issue, and the most expensive
sessions. It is one static HTML file at ``<vault>/.lore/stats.html``.
See ``docs/how-to/measure-token-use.md``.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime

import typer
from lore_core.stats_page import write_page
from rich.console import Console

from lore_cli._argv_compat import argv_main

console = Console()

app = typer.Typer(
    add_completion=False,
    help=__doc__,
    no_args_is_help=False,
    rich_markup_mode="rich",
)


@app.callback(invoke_without_command=True)
def stats(
    days: int = typer.Option(30, "--days", help="Days to sum; 0 for every record."),
    no_open: bool = typer.Option(False, "--no-open", help="Write the page; do not open it."),
) -> None:
    """Write the token-statistics page and open it."""
    import webbrowser

    from lore_core.config import get_lore_root

    try:
        lore_root = get_lore_root()
    except Exception:  # noqa: BLE001
        console.print("[red]LORE_ROOT not set.[/red] Run `lore setup` or export $LORE_ROOT.")
        raise typer.Exit(1) from None
    path = write_page(lore_root, days=days, now=datetime.now(UTC))
    print(path)
    if not no_open:
        webbrowser.open(path.as_uri())


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
