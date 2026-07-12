"""`lore migrate` — one-shot upgrade paths for existing vaults.

Three verbs, all idempotent and all dry-run by default:

* ``lore migrate frontmatter`` — schema-evolution rewrites of note frontmatter
* ``lore migrate slugs``       — rename session notes whose filename slug is cryptic
* ``lore migrate open-items``  — rewrite legacy `## Open items` sections to v2

These are upgrades you run once when a vault falls behind the current
schema. `lore backfill` is unrelated — that imports historical
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
from rich.console import Console

from lore_cli._argv_compat import argv_main

console = Console()
err_console = Console(stderr=True)

app = typer.Typer(
    add_completion=False,
    help="One-shot upgrades: frontmatter schema, session slugs, open items.",
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


@app.command("open-items")
def cmd_open_items(
    wiki: str = typer.Option(None, "--wiki", "-w", help="Scope to a single wiki."),
    apply: bool = typer.Option(
        False, "--apply", help="Actually write changes. Without this, runs dry."
    ),
) -> None:
    """Interactive v1 → v2 migration for `## Open items` session sections."""
    from lore_curator.open_items_migration import run_open_items_migration

    run_open_items_migration(wiki_filter=wiki, dry_run=not apply)


@app.command("slugs")
def cmd_slugs(
    wiki: str = typer.Option(
        None, "--wiki", "-w", help="Scope to one wiki. Default: every wiki under lore_root.",
    ),
    apply: bool = typer.Option(
        False,
        "--apply/--dry-run",
        help="Actually rename + write aliases. Default: dry-run that prints the plan.",
    ),
) -> None:
    """One-shot rename of session notes whose slug is cryptic.

    Walks ``wiki/<name>/sessions/`` and renames any non-stub session
    note whose filename slug differs from ``_slug(title)``. The old
    stem is preserved as a frontmatter ``aliases:`` entry so existing
    ``[[old-stem]]`` references keep resolving.

    Skips:

    * stubs awaiting synthesis (``state: stub``)
    * continuation chains (``part >= 2`` or ``continues:``)
    * notes without a real title (placeholder or empty)
    * notes whose filename already matches the title-derived slug
    """
    from lore_curator.backfill_slugs import backfill_wiki

    from lore_cli._cli_helpers import lore_root_or_die

    lore_root = lore_root_or_die(err_console)
    wikis = [wiki] if wiki else _discover_wikis(lore_root)
    if not wikis:
        err_console.print("[yellow]no wikis found under lore_root[/yellow]")
        raise typer.Exit(code=1)

    grand_planned = 0
    for w in wikis:
        wiki_path = lore_root / "wiki" / w
        if not wiki_path.exists():
            err_console.print(f"[yellow]skip[/yellow] {w}: not found at {wiki_path}")
            continue
        report = backfill_wiki(wiki_path, apply=apply)
        verb = "would rename" if not apply else "renamed"
        console.print(
            f"[bold]{w}[/bold] — scanned={report.scanned}, "
            f"{verb}={len(report.planned) if not apply else len(report.renamed)}, "
            f"skipped(stub={report.skipped_stub}, "
            f"chain={report.skipped_chain}, "
            f"no-title={report.skipped_no_title}, "
            f"canonical={report.skipped_already_canonical})"
        )
        for plan in report.planned:
            arrow = "→" if not apply else "✓"
            console.print(
                f"  {arrow} {plan.old_path.name} → {plan.new_path.name}"
                f"  [dim]({plan.title})[/dim]"
            )
        for path, reason in report.failed:
            console.print(f"  [red]fail[/red] {path.name}: {reason}")
        grand_planned += len(report.planned)

    if not apply and grand_planned:
        console.print(
            f"\n[dim]dry-run; pass --apply to rename {grand_planned} note(s).[/dim]"
        )


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
