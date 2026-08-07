"""`lore migrate` — one-shot upgrade paths for existing vaults.

One verb, idempotent and dry-run by default:

* ``lore migrate frontmatter`` — schema-evolution rewrites of note frontmatter

This is an upgrade you run once when a vault falls behind the current
schema. It is unrelated to the retired `lore backfill`, which imported historical
transcripts.
"""

from __future__ import annotations

import sys
from pathlib import Path

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
    help="One-shot upgrades: frontmatter schema.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _discover_wikis(lore_root: Path) -> list[str]:
    """Return sorted list of wiki directory names under lore_root/wiki/."""
    wiki_dir = lore_root / "wiki"
    if not wiki_dir.exists():
        return []
    return sorted([d.name for d in wiki_dir.iterdir() if d.is_dir()])


@app.command("frontmatter")
def cmd_frontmatter(
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
    if add_schema_version_:
        add_schema_version(wiki_filter=wiki, dry_run=not apply)
        return
    if minimal_status:
        migrate_minimal_status(wiki_filter=wiki, dry_run=not apply)
        return
    if strip_broken_wikilinks:
        migrate_strip_broken_wikilinks(wiki_filter=wiki, dry_run=not apply)
        return
    print(ctx.get_help())
    raise typer.Exit(code=2)


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
