"""`lore migrate` — one-shot frontmatter migrations."""

from __future__ import annotations

import sys

import typer
from lore_core.migrate import (
    add_schema_version,
    migrate_minimal_status,
    migrate_strip_broken_wikilinks,
)
from lore_core.schema import SCHEMA_VERSION

from lore_cli._argv_compat import argv_main

app = typer.Typer(
    add_completion=False,
    help="Frontmatter migrations for Lore schema evolution.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


@app.callback(invoke_without_command=True)
def migrate(
    ctx: typer.Context,
    wiki: str = typer.Option(None, "--wiki", "-w", help="Scope to a single wiki."),
    add_schema_version_: bool = typer.Option(
        False,
        "--add-schema-version",
        help=f"Add `schema_version: {SCHEMA_VERSION}` to notes missing it.",
    ),
    minimal_status: bool = typer.Option(
        False,
        "--minimal-status",
        help="Drop `status:` field per status-vocabulary-minimalism "
        "(proposed → draft: true, others dropped).",
    ),
    strip_broken_wikilinks: bool = typer.Option(
        False,
        "--strip-broken-wikilinks",
        help="Convert `[[broken]]` (target doesn't exist) to plain text "
        "across all note bodies. `[[slug|alias]]` becomes the alias.",
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Actually write changes. Without this, runs dry.",
    ),
) -> None:
    """Run a frontmatter migration. Pick exactly one with a flag."""
    # A subcommand will handle its own invocation; do nothing here.
    if ctx.invoked_subcommand is not None:
        return
    if add_schema_version_:
        add_schema_version(wiki_filter=wiki, dry_run=not apply)
        return
    if minimal_status:
        migrate_minimal_status(wiki_filter=wiki, dry_run=not apply)
        return
    if strip_broken_wikilinks:
        migrate_strip_broken_wikilinks(wiki_filter=wiki, dry_run=not apply)
        return
    # No migration flag and no subcommand → show help
    print(ctx.get_help())
    raise typer.Exit(code=2)


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
