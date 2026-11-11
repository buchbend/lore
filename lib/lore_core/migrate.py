"""One-shot frontmatter migrations for Lore's schema evolution.

Migrations are idempotent (re-running is a no-op). Each is driven by a
CLI flag on `lore migrate frontmatter`. Most users only need `--add-schema-version`
once after upgrading to this version.
"""

from __future__ import annotations

from rich.console import Console

from lore_core.io import atomic_write_text
from lore_core.wiki_check import SKIP_DIRS, SKIP_FILES, discover_notes, discover_wikis
from lore_core.schema import SCHEMA_VERSION, parse_frontmatter, split_frontmatter

console = Console()


_split_frontmatter = split_frontmatter


def add_schema_version(
    wiki_filter: str | None = None,
    dry_run: bool = True,
) -> int:
    """Prepend `schema_version: N` to the frontmatter of every note missing it.

    Returns the count of notes patched (or that would be patched if dry-run).
    Notes without any frontmatter block are skipped (they're malformed;
    `lore wiki check` reports them).
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
            "(`lore wiki check` reports these)."
        )
    if dry_run and patched:
        console.print("[dim]Re-run with --apply to write changes.[/dim]")
    return patched


# ---------------------------------------------------------------------------
# Strip broken wikilinks — body migration
# ---------------------------------------------------------------------------


def migrate_strip_broken_wikilinks(
    wiki_filter: str | None = None,
    dry_run: bool = True,
) -> dict:
    """Convert broken ``[[wikilinks]]`` to plain text across the vault.

    A wikilink is "broken" when its target slug doesn't match any
    existing ``.md`` file in the wiki. ``[[Foo Bar]]`` becomes the
    plain text ``Foo Bar``; ``[[slug|displayed]]`` becomes ``displayed``
    — text content survives, only the bracketing is removed.

    Frontmatter, fenced code blocks, and inline code spans are
    preserved verbatim. Idempotent.

    Returns ``{"files": N, "replacements": M, "by_target": {target: count}}``.
    """
    from collections import Counter

    from lore_core.wikilinks import existing_slugs, strip_broken_wikilinks

    wikis = discover_wikis(wiki_filter)
    files_touched = 0
    total_replacements = 0
    by_target: Counter[str] = Counter()

    for wiki_path in wikis:
        wiki_name = wiki_path.name
        # Per-wiki scoping: wikis are portable units; cross-wiki
        # references break on extraction and are treated as dangles.
        slugs = existing_slugs(wiki_path)
        for fpath in discover_notes(wiki_path):
            if fpath.name in SKIP_FILES:
                continue
            if any(part in SKIP_DIRS for part in fpath.parts):
                continue
            try:
                text = fpath.read_text(errors="replace")
            except OSError:
                continue
            new_text, n, replaced = strip_broken_wikilinks(text, slugs)
            if n == 0:
                continue
            files_touched += 1
            total_replacements += n
            by_target.update(replaced)
            rel = fpath.relative_to(wiki_path)
            if dry_run:
                console.print(f"[dim]would strip {n:2d}[/dim] {wiki_name}/{rel}")
            else:
                atomic_write_text(fpath, new_text)
                console.print(f"[green]stripped {n:2d}[/green] {wiki_name}/{rel}")

    verb = "would strip" if dry_run else "stripped"
    console.print()
    console.print(
        f"[bold]{verb} {total_replacements} broken wikilinks[/bold] across {files_touched} file(s)."
    )
    if by_target:
        console.print()
        console.print("[bold]Top stripped targets:[/bold]")
        for target, count in by_target.most_common(15):
            console.print(f"  {count:4d}  [[{target}]]")
    if dry_run and total_replacements:
        console.print("[dim]Re-run with --apply to write changes.[/dim]")

    return {
        "files": files_touched,
        "replacements": total_replacements,
        "by_target": dict(by_target),
    }
