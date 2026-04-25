"""One-shot frontmatter migrations for Lore's schema evolution.

Migrations are idempotent (re-running is a no-op). Each is driven by a
CLI flag on `python -m lore_core.migrate`. Most users only need
`--add-schema-version` once after upgrading to this version.
"""

from __future__ import annotations

import sys

import typer
from rich.console import Console

from lore_runtime.argv import argv_main

from lore_core.io import atomic_write_text
from lore_core.lint import SKIP_DIRS, SKIP_FILES, discover_notes, discover_wikis
from lore_core.schema import SCHEMA_VERSION, parse_frontmatter

console = Console()


def _split_frontmatter(text: str) -> tuple[str, str] | None:
    """Return (frontmatter_block_without_delimiters, body) or None if absent."""
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    fm = text[4:end]
    body = text[end + 4 :].lstrip("\n")
    return fm, body


def add_schema_version(
    wiki_filter: str | None = None,
    dry_run: bool = True,
) -> int:
    """Prepend `schema_version: N` to the frontmatter of every note missing it.

    Returns the count of notes patched (or that would be patched if dry-run).
    Notes without any frontmatter block are skipped (they're malformed; lint
    reports them separately).
    """
    wikis = discover_wikis(wiki_filter)
    patched = 0
    skipped_no_fm = 0

    for wiki_path in wikis:
        wiki_name = wiki_path.name
        for fpath in discover_notes(wiki_path):
            if fpath.name in SKIP_FILES:
                continue
            if any(part in SKIP_DIRS for part in fpath.parts):
                continue
            text = fpath.read_text(errors="replace")
            fm = parse_frontmatter(text)
            if not fm:
                skipped_no_fm += 1
                continue
            if "schema_version" in fm:
                continue
            split = _split_frontmatter(text)
            if split is None:
                skipped_no_fm += 1
                continue
            fm_block, body = split
            new_text = f"---\nschema_version: {SCHEMA_VERSION}\n{fm_block}\n---\n{body}"
            rel = fpath.relative_to(wiki_path)
            if dry_run:
                console.print(f"[dim]would patch[/dim] {wiki_name}/{rel}")
            else:
                atomic_write_text(fpath, new_text)
                console.print(f"[green]patched[/green] {wiki_name}/{rel}")
            patched += 1

    verb = "would patch" if dry_run else "patched"
    console.print()
    console.print(f"[bold]{verb} {patched} notes[/bold] across {len(wikis)} wiki(s).")
    if skipped_no_fm:
        console.print(
            f"[yellow]Skipped {skipped_no_fm} files with no frontmatter[/yellow] "
            "(lint reports these separately)."
        )
    if dry_run and patched:
        console.print("[dim]Re-run with --apply to write changes.[/dim]")
    return patched


# ---------------------------------------------------------------------------
# `status:` → draft/superseded_by (status-vocabulary-minimalism)
# ---------------------------------------------------------------------------


# `status:` values that mean "canonical" under the minimal regime — drop
# the field entirely.
_STATUS_TO_DROP: frozenset[str] = frozenset(
    {"active", "stable", "accepted", "stale", "implemented", "partial", "abandoned"}
)


def _minimize_status_text(text: str) -> tuple[str, str | None]:
    """Return (new_text, warning). warning is non-None when a note needs review.

    Mapping (status-vocabulary-minimalism):
      - active | stable | accepted | stale | implemented | partial | abandoned
          → drop `status:` field (canonical).
      - proposed
          → drop `status:`, set `draft: true`.
      - superseded
          → drop `status:`; keep existing `superseded_by:` if present,
            otherwise emit a warning (caller decides how to surface).

    Idempotent: a note without `status:` is returned unchanged.
    """
    import yaml

    if not text.startswith("---"):
        return text, None
    end = text.find("\n---", 3)
    if end == -1:
        return text, None
    fm_block = text[4:end]
    body = text[end + 4 :]

    fm = yaml.safe_load(fm_block) or {}
    if "status" not in fm:
        return text, None

    status = str(fm.pop("status") or "").strip().lower()
    warning: str | None = None

    if status in _STATUS_TO_DROP:
        pass  # already popped
    elif status == "proposed":
        fm["draft"] = True
    elif status == "superseded":
        if not fm.get("superseded_by"):
            warning = "status: superseded with no superseded_by: target — add one manually"
    else:
        # Unknown / empty status — drop it but flag for review
        warning = f"unrecognized status {status!r} dropped"

    new_fm = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).rstrip() + "\n"
    return f"---\n{new_fm}---{body}", warning


def migrate_minimal_status(
    wiki_filter: str | None = None,
    dry_run: bool = True,
) -> int:
    """Rewrite `status:` per status-vocabulary-minimalism across wiki notes.

    Idempotent. Returns the count of notes touched (or that would be).
    """
    wikis = discover_wikis(wiki_filter)
    touched = 0
    warnings = 0

    for wiki_path in wikis:
        wiki_name = wiki_path.name
        for fpath in discover_notes(wiki_path):
            if fpath.name in SKIP_FILES:
                continue
            if any(part in SKIP_DIRS for part in fpath.parts):
                continue
            text = fpath.read_text(errors="replace")
            new_text, warning = _minimize_status_text(text)
            if new_text == text:
                continue
            rel = fpath.relative_to(wiki_path)
            if dry_run:
                console.print(f"[dim]would rewrite[/dim] {wiki_name}/{rel}")
            else:
                atomic_write_text(fpath, new_text)
                console.print(f"[green]rewrote[/green] {wiki_name}/{rel}")
            touched += 1
            if warning:
                console.print(f"  [yellow]warning[/yellow] {wiki_name}/{rel}: {warning}")
                warnings += 1

    verb = "would rewrite" if dry_run else "rewrote"
    console.print()
    console.print(f"[bold]{verb} {touched} notes[/bold] across {len(wikis)} wiki(s).")
    if warnings:
        console.print(f"[yellow]{warnings} notes need manual review[/yellow]")
    if dry_run and touched:
        console.print("[dim]Re-run with --apply to write changes.[/dim]")
    return touched


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
    # No migration flag and no subcommand → show help
    print(ctx.get_help())
    raise typer.Exit(code=2)


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
