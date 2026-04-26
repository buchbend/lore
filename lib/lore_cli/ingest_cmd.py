"""`lore ingest` — ingest a JSONL transcript via ManualSendAdapter.

Reads a JSONL transcript from a file path or stdin (`-`) and upserts a
ledger entry so Curator A can pick it up.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console

console = Console()
err_console = Console(stderr=True)

app = typer.Typer(
    add_completion=False,
    help="Ingest a JSONL transcript into the ledger.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)


@app.callback(invoke_without_command=True)
def ingest(
    from_path: str = typer.Option(..., "--from", help="Path to JSONL file, or '-' for stdin."),
    integration: str = typer.Option(
        ...,
        "--integration",
        help="Declared source integration (e.g. cursor, copilot).",
    ),
    directory: str = typer.Option(..., "--directory", help="Working directory of the transcript session."),
    transcript_id: str = typer.Option(None, "--transcript-id", help="Override transcript ID (defaults to filename+mtime)."),
) -> None:
    """Read a JSONL transcript and upsert a sidecar ledger entry."""
    from lore_adapters import get_adapter
    from lore_core.ledger import TranscriptLedger, TranscriptLedgerEntry
    from lore_core.types import TranscriptHandle

    lore_root_str = os.environ.get("LORE_ROOT", "")
    if not lore_root_str:
        err_console.print("[red]Error:[/red] LORE_ROOT environment variable not set.")
        raise typer.Exit(1)

    lore_root = Path(lore_root_str)
    cwd = Path(directory).resolve()
    now = datetime.now(UTC)

    adapter = get_adapter("manual-send")

    # Determine source and transcript_id
    if from_path == "-":
        source = sys.stdin
        tid = transcript_id or f"stdin-{now.strftime('%Y%m%dT%H%M%S')}"
        source_path = Path(f"<stdin>")
    else:
        file_path = Path(from_path).resolve()
        source = file_path
        if transcript_id:
            tid = transcript_id
        else:
            mtime_ts = int(file_path.stat().st_mtime) if file_path.exists() else 0
            tid = f"{file_path.stem}-{mtime_ts}"
        source_path = file_path

    # Parse turns
    try:
        turns = list(adapter.read_from(source, cwd, declared_integration=integration))
    except ValueError as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)

    # Report turns
    for turn in turns:
        console.print(f"  Turn index={turn.index} role={turn.role}")

    # Upsert ledger entry
    ledger = TranscriptLedger(lore_root)
    entry = TranscriptLedgerEntry(
        integration="manual-send",
        transcript_id=tid,
        path=source_path,
        directory=cwd,
        digested_hash=None,
        digested_index_hint=None,
        synthesised_hash=None,
        last_mtime=now,
        curator_a_run=None,
        noteworthy=None,
        session_note=None,
    )
    ledger.upsert(entry)

    console.print(
        f"[green]Ingested[/green] {len(turns)} turn(s) → ledger entry "
        f"[bold]{entry.integration}::{tid}[/bold]"
    )
