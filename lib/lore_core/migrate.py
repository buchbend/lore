"""One-shot frontmatter migrations for Lore's schema evolution.

Migrations are idempotent (re-running is a no-op). Each is driven by a
CLI flag on `lore migrate`. Most users only need `--add-schema-version`
once after upgrading to this version.
"""

from __future__ import annotations

from rich.console import Console

from lore_core.io import atomic_write_text
from lore_core.lint import SKIP_DIRS, SKIP_FILES, discover_notes, discover_wikis
from lore_core.schema import SCHEMA_VERSION, parse_frontmatter, split_frontmatter

console = Console()


_split_frontmatter = split_frontmatter


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
        f"[bold]{verb} {total_replacements} broken wikilinks[/bold] "
        f"across {files_touched} file(s)."
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
