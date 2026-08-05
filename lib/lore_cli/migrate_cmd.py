"""`lore migrate` — one-shot upgrade paths for existing vaults.

Three verbs, all idempotent and all dry-run by default:

* ``lore migrate frontmatter`` — schema-evolution rewrites of note frontmatter
* ``lore migrate slugs``       — rename session notes whose filename slug is cryptic
* ``lore migrate open-items``  — rewrite legacy `## Open items` sections to v2
* ``lore migrate retire-session-notes`` — backfill the transcript ledger's
  linkage blocks, then delete the session-note stock

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


@app.command("retire-session-notes")
def cmd_retire_session_notes(
    wiki: str = typer.Option(
        None,
        "--wiki",
        "-w",
        help="Scope the deletion to one wiki. Default: every wiki.",
    ),
    apply: bool = typer.Option(
        False, "--apply", help="Actually backfill and delete. Without this, runs dry."
    ),
) -> None:
    """Retire session notes into the transcript ledger.

    Backfills each archived transcript's linkage block (repo, branch,
    PRs, issues, commits, files) from the transcript and git, then
    deletes the session-note files under every wiki's ``sessions/``
    tree.

    ``--wiki`` scopes the deletion. The backfill always covers the whole
    ledger — it is one machine-local store, not a per-wiki one, and
    stamping a linkage block is additive.

    Capture still writes new session notes into ``sessions/`` at every
    session boundary; the stock this deletes starts refilling until the
    compose pipeline is retired.

    Prints the plan and changes nothing unless ``--apply`` is passed.
    Notes are markdown in git — recover a mistaken run from the wiki's
    history.
    """
    from lore_curator.retire_session_notes import apply_retirement, plan_retirement

    from lore_cli._cli_helpers import lore_root_or_die

    lore_root = lore_root_or_die(err_console)
    plan = plan_retirement(lore_root, wiki=wiki)

    with_refs = sum(
        1 for i in plan.backfill if i.linkage.get("issues") or i.linkage.get("prs")
    )
    unreadable = sum(1 for i in plan.backfill if i.transcript_missing)
    # soft_wrap keeps every path on one logical line: a deletion plan is
    # only useful if the paths in it can be read and pasted verbatim.
    console.print(
        f"[bold]Backfill[/bold] — {len(plan.backfill)} ledger entries "
        f"({with_refs} with issue/PR refs, {unreadable} without a readable transcript)",
        highlight=False,
    )
    console.print(
        f"[bold]Delete[/bold] — {len(plan.deletions)} session note(s)", highlight=False
    )
    for path in plan.deletions:
        console.print(f"  [red]-[/red] {path}", highlight=False, soft_wrap=True)
    for path in plan.kept:
        console.print(
            f"  [dim]keep (not a note)[/dim] {path}", highlight=False, soft_wrap=True
        )

    if not apply:
        console.print(
            "\n[dim]dry-run; nothing changed. Pass --apply to backfill and delete.[/dim]"
        )
        console.print(
            "[dim]Capture still writes new session notes until the compose "
            "pipeline is retired.[/dim]",
            soft_wrap=True,
        )
        return

    report = apply_retirement(lore_root, plan)
    console.print(
        f"\n[green]Backfilled {report.backfilled} ledger entries; "
        f"deleted {report.deleted} note(s).[/green]"
    )
    for path, reason in report.failed:
        console.print(f"  [red]fail[/red] {path}: {reason}")


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
