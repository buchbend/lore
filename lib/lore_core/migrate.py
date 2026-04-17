"""One-shot frontmatter migrations for Lore's schema evolution.

Migrations are idempotent (re-running is a no-op). Each is driven by a
CLI flag on `python -m lore_core.migrate`. Most users only need
`--add-schema-version` once after upgrading to this version.
"""

from __future__ import annotations

import argparse
import sys

from rich.console import Console

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lore-migrate", description="Frontmatter migrations for Lore schema evolution."
    )
    parser.add_argument("--wiki", "-w", help="Scope to a single wiki")
    parser.add_argument(
        "--add-schema-version",
        action="store_true",
        help=f"Add `schema_version: {SCHEMA_VERSION}` to notes missing it",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write changes. Without this, runs dry.",
    )
    args = parser.parse_args(argv)

    if not args.add_schema_version:
        parser.print_help()
        return 2

    add_schema_version(wiki_filter=args.wiki, dry_run=not args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
