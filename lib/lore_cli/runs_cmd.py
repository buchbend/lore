"""`lore runs` — inspect Curator A run logs."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

from lore_core.run_reader import (
    RunIdAmbiguous, RunIdNotFound, SchemaVersionTooNew,
    read_run, resolve_run_id,
)
from lore_cli.run_render import (
    pick_icon_set, render_flat_log, render_summary_panel, should_use_color,
)


app = typer.Typer(
    add_completion=False,
    help=(
        "Inspect curator run logs.\n\n"
        "Scenarios:\n"
        "  no note appeared?         lore runs show latest\n"
        "  hook plumbing feels off?  lore doctor\n"
        "  tuning config?            lore curator run --dry-run --trace-llm"
    ),
    no_args_is_help=True,
    rich_markup_mode="rich",
)
console = Console()


def _get_lore_root() -> Path:
    from lore_core.config import get_lore_root
    return get_lore_root()


@app.command("list")
def list_runs() -> None:
    """List recent runs (most recent first)."""
    lore_root = _get_lore_root()
    from lore_core.run_reader import _list_runs
    runs = _list_runs(lore_root)
    if not runs:
        console.print("[yellow]No runs on disk.[/yellow]")
        return
    for path in reversed(runs):
        console.print(path.stem)


@app.command("show")
def show(
    run_id: str = typer.Argument(..., help="latest | ^N | short suffix | full ID | prefix"),
    verbose: bool = typer.Option(False, "--verbose", help="Include LLM prompts/responses"),
    raw: bool = typer.Option(False, "--raw", help="Disable 3-line trace truncation (requires --verbose)"),
    json_out: bool = typer.Option(False, "--json", help="Print raw JSONL"),
) -> None:
    lore_root = _get_lore_root()
    try:
        path = resolve_run_id(lore_root, run_id)
    except RunIdNotFound as e:
        console.print(f"[red]Run not found: {e}. Try `lore runs list`.[/red]")
        raise typer.Exit(code=1)
    except RunIdAmbiguous as e:
        console.print(f"[yellow]Ambiguous — matches:[/yellow] {', '.join(e.matches)}")
        raise typer.Exit(code=1)

    if json_out:
        sys.stdout.write(path.read_text())
        return

    try:
        records = read_run(path, strict_schema=True)
    except SchemaVersionTooNew as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)

    if verbose:
        trace_path = path.parent / f"{path.stem}.trace.jsonl"
        if not trace_path.exists():
            console.print(
                "[yellow]LLM trace not captured for this run. "
                "Re-run with [bold]LORE_TRACE_LLM=1 lore curator run --dry-run[/bold] "
                "to capture.[/yellow]"
            )
        else:
            trace_records = read_run(trace_path, strict_schema=True)
            records = sorted(records + trace_records, key=lambda r: r.get("ts", ""))

    term_width = shutil.get_terminal_size((80, 20)).columns
    icons = pick_icon_set()
    use_color = should_use_color()

    panel_lines = render_summary_panel(records, term_width=term_width)
    short_id = path.stem.split("-")[-1]
    header = f"Run {short_id} ({path.stem})"

    if use_color and sys.stdout.isatty():
        console.print(Panel("\n".join(panel_lines), title=header, expand=False))
    else:
        console.print(header)
        for ln in panel_lines:
            console.print(ln)

    flat = render_flat_log(records, icons=icons, use_color=use_color)
    console.print(flat)
