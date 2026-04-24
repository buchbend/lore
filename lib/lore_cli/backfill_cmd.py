"""`lore backfill` — opt-in processing of historical transcripts."""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    add_completion=False,
    help=(
        "Process historical transcripts that were skipped on attach.\n\n"
        "By default shows a dry-run preview. Use --confirm to process."
    ),
    no_args_is_help=False,
    rich_markup_mode="rich",
)

console = Console()


def _get_lore_root() -> Path:
    from lore_core.config import get_lore_root
    return get_lore_root()


def _short_dir(p: Path) -> str:
    try:
        home = Path.home()
        if p.is_relative_to(home):
            return "~/" + str(p.relative_to(home))
    except (AttributeError, ValueError):
        pass
    return str(p)


@app.callback(invoke_without_command=True)
def backfill(
    wiki: str = typer.Option(None, "--wiki", help="Limit to one wiki."),
    scope: str = typer.Option(None, "--scope", help="Limit to transcripts matching this scope prefix."),
    exclude_scope: str = typer.Option(None, "--exclude-scope", help="Exclude transcripts matching this scope prefix."),
    since: str = typer.Option(None, "--since", help="Only transcripts after this date (YYYY-MM-DD)."),
    confirm: bool = typer.Option(False, "--confirm", help="Actually queue transcripts for processing."),
) -> None:
    """Preview or queue historical transcripts for curator processing."""
    from lore_core.ledger import TranscriptLedger
    from lore_core.state.attachments import AttachmentsFile
    from lore_core.scope_resolver import resolve_scope

    lore_root = _get_lore_root()
    tledger = TranscriptLedger(lore_root)
    af = AttachmentsFile(lore_root)
    af.load()

    since_dt = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since).replace(tzinfo=UTC)
        except ValueError:
            console.print(f"[red]Invalid date: {since}. Use YYYY-MM-DD.[/red]")
            raise typer.Exit(code=1)

    candidates = []
    already_have_notes = 0
    skipped_by_wiki: dict[str, int] = defaultdict(int)
    for entry in tledger.all_entries():
        if entry.orphan:
            continue
        attachment = af.longest_prefix_match(entry.directory)
        if attachment is None:
            continue
        if entry.last_mtime >= attachment.attached_at:
            continue

        resolved = resolve_scope(entry.directory)
        resolved_wiki = resolved.wiki if resolved else "__unattached__"
        resolved_scope = resolved.scope if resolved else ""

        if wiki and resolved_wiki != wiki:
            continue
        if scope and not resolved_scope.startswith(scope):
            continue
        if exclude_scope and resolved_scope.startswith(exclude_scope):
            continue

        if entry.session_note is not None:
            already_have_notes += 1
            skipped_by_wiki[resolved_wiki] += 1
            continue

        if since_dt and entry.last_mtime < since_dt:
            continue

        candidates.append((entry, resolved_wiki, resolved_scope or "—"))

    if not candidates and already_have_notes == 0:
        console.print("[dim]No historical transcripts found.[/dim]")
        console.print("[dim]Historical transcripts are detected on attach — attach a repo first.[/dim]")
        return

    candidates.sort(key=lambda t: t[0].last_mtime)

    scope_label = f" for {wiki}" if wiki else ""
    console.print(f"Scanning historical transcripts{scope_label}...\n")

    # Group by wiki for summary
    by_wiki: dict[str, list] = defaultdict(list)
    for entry, resolved_wiki, scope_str in candidates:
        by_wiki[resolved_wiki].append((entry, scope_str))

    for wname in sorted(by_wiki):
        entries = by_wiki[wname]
        oldest = min(e.last_mtime for e, _ in entries).strftime("%Y-%m-%d")
        newest = max(e.last_mtime for e, _ in entries).strftime("%Y-%m-%d")
        skipped = skipped_by_wiki.get(wname, 0)
        skip_str = f"  ({skipped} already have notes)" if skipped else ""
        console.print(f"  [cyan]{wname}[/cyan]: {len(entries)} session(s), {oldest} — {newest}{skip_str}")

        # Group by scope within wiki
        by_scope: dict[str, list] = defaultdict(list)
        for entry, scope_str in entries:
            by_scope[scope_str].append(entry)
        for sname in sorted(by_scope):
            scope_entries = by_scope[sname]
            dirs = {_short_dir(e.directory) for e in scope_entries}
            dir_str = ", ".join(sorted(dirs)[:3])
            if len(dirs) > 3:
                dir_str += f" (+{len(dirs) - 3})"
            console.print(f"    {sname:30s}  {len(scope_entries):3d} sessions  {dir_str}")

    total = len(candidates)
    console.print()
    console.print(f"  Total: {total} session(s) to process")
    if already_have_notes:
        console.print(f"  Skipped: {already_have_notes} already have notes")

    if not candidates:
        console.print("\n[dim]Nothing to process.[/dim]")
        return

    if not confirm:
        newest = candidates[-1][0].last_mtime.strftime("%Y-%m-%d")
        console.print(f"\n  Run [cyan]lore backfill --confirm[/cyan] to queue all.")
        if not since:
            console.print(f"  Limit: [cyan]lore backfill --since {newest[:7]}-01 --confirm[/cyan]")
        return

    reset_count = 0
    for entry, _, _ in candidates:
        entry.curator_a_run = None
        entry.digested_hash = None
        entry.digested_index_hint = None
        entry.noteworthy = None
        reset_count += 1
    tledger.bulk_upsert([e for e, _, _ in candidates])

    console.print(f"\n  [green]Queued {reset_count} transcript(s).[/green]")
    console.print("  Curator will process them on the next session boundary,")
    console.print("  or run [cyan]lore curator run[/cyan] to process now.")
