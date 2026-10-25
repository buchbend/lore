"""`lore stats` — write the status pages and open one in the browser.

    lore stats
    lore stats --days 7
    lore stats --page systems
    lore stats --no-open

The token page sums the token records that the SessionEnd hook writes: cost
per day, by model, by workflow stage, by epic or issue, and the most
expensive sessions. The systems page draws the system maps of the wiki and
the checkouts of each scope. Both are static HTML files in
``<vault>/.lore/``: ``stats.html`` and ``systems.html``. See
``docs/how-to/measure-token-use.md`` and ``docs/how-to/keep-a-system-map.md``.
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
    page: str = typer.Option("tokens", "--page", help="The page to open: `tokens` or `systems`."),
    no_open: bool = typer.Option(False, "--no-open", help="Write the pages; do not open one."),
) -> None:
    """Write the status pages and open one."""
    import webbrowser

    from lore_core import systems_page
    from lore_core.config import get_lore_root

    if page not in ("tokens", "systems"):
        console.print(f"[red]Unknown page {page!r}.[/red] Pass `tokens` or `systems`.")
        raise typer.Exit(2)

    try:
        lore_root = get_lore_root()
    except Exception:  # noqa: BLE001
        console.print("[red]LORE_ROOT not set.[/red] Run `lore setup` or export $LORE_ROOT.")
        raise typer.Exit(1) from None
    path = write_page(lore_root, days=days, now=datetime.now(UTC))
    systems = systems_page.write_page(lore_root)
    if page == "systems":
        path = systems
    print(path)
    if not no_open:
        webbrowser.open(path.as_uri())


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
