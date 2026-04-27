"""`lore lint` — health-check the vault, regenerate catalogs."""

from __future__ import annotations

import sys

import typer
from lore_core.lint import run_lint

from lore_cli._argv_compat import argv_main

app = typer.Typer(
    add_completion=False,
    help="Lore linter — scan all wikis, check health, regenerate catalogs.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)


@app.callback(invoke_without_command=True)
def lint(
    wiki: str = typer.Option(None, "--wiki", "-w", help="Scope to a single wiki."),
    check_only: bool = typer.Option(
        False, "--check-only", help="Lint only, skip catalog writes."
    ),
    json_out: bool = typer.Option(False, "--json", help="Output report as JSON."),
) -> None:
    """Lint the vault and (re)generate catalogs."""
    report = run_lint(
        wiki_filter=wiki,
        check_only=check_only,
        json_output=json_out,
    )
    if report.get("by_severity", {}).get("errors", 0) > 0:
        raise typer.Exit(code=1)


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
