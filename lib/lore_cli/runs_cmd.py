"""`lore runs` — inspect Curator A run logs."""

from __future__ import annotations

import json
import shutil
import sys
import time
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

_POLL_INTERVAL_S = 0.2
_IDLE_TIMEOUT_S = 30 * 60  # 30 min


def _get_lore_root() -> Path:
    from lore_core.config import get_lore_root
    return get_lore_root()


def _complete_run_id(ctx, args, incomplete: str):
    """Return matching run-id suffixes + static aliases for shell completion.

    Signature: (ctx: click.Context, args: list[str], incomplete: str) -> list[str]
    Compatible with typer's ``autocompletion=`` parameter.
    """
    try:
        from lore_core.config import get_lore_root
        runs_dir = get_lore_root() / ".lore" / "runs"
        suffixes = [
            p.stem.split("-")[-1]
            for p in runs_dir.glob("*.jsonl")
            if not p.name.endswith(".trace.jsonl")
        ]
    except Exception:
        suffixes = []
    candidates = suffixes + ["latest"] + [f"^{i}" for i in range(1, 6)]
    return [c for c in candidates if c.startswith(incomplete)]


@app.command("list")
def list_runs(
    limit: int = typer.Option(20, "--limit", help="Maximum runs to show."),
    hooks: bool = typer.Option(False, "--hooks", help="Interleave hook events."),
    json_out: bool = typer.Option(False, "--json", help="Print raw JSONL."),
) -> None:
    """List recent runs (most recent first)."""
    from datetime import UTC, datetime
    from rich.table import Table

    lore_root = _get_lore_root()

    runs_dir = lore_root / ".lore" / "runs"

    if hooks:
        import os as _os
        # Build combined list of (ts_str, kind, data) tuples.
        combined: list[tuple[str, str, object]] = []

        # Load runs.
        if runs_dir.exists():
            archival_paths = sorted(
                (p for p in runs_dir.glob("*.jsonl") if not p.name.endswith(".trace.jsonl")),
                key=lambda p: p.name,
                reverse=True,
            )[:limit]
            for p in archival_paths:
                records = read_run(p, strict_schema=False)
                start = next((r for r in records if r.get("type") == "run-start"), {})
                end = next((r for r in reversed(records) if r.get("type") == "run-end"), {})
                ts = start.get("ts", "")
                short_id = p.stem.split("-")[-1]
                schema_mismatch = any(r.get("_schema_mismatch") for r in records)
                dur = f"{end.get('duration_ms', 0) / 1000:.1f}s"
                notes_new = end.get("notes_new", 0)
                notes_merged = end.get("notes_merged", 0)
                skipped = end.get("skipped", 0)
                errors = end.get("errors", 0)
                if notes_new == 0 and notes_merged == 0:
                    summary = f"0 skipped ({skipped})" if skipped else "0 \u00b7 0 errors"
                else:
                    summary = f"{notes_new} new" + (f"+{notes_merged}m" if notes_merged else "")
                    summary += f" \u00b7 {errors} errors"
                combined.append((ts, "run", {
                    "short_id": short_id,
                    "started": _relative_time_cli(ts),
                    "dur": dur,
                    "summary": summary,
                    "schema_mismatch": schema_mismatch,
                }))

        # Load hook events.
        events_path = lore_root / ".lore" / "hook-events.jsonl"
        if events_path.exists():
            try:
                for line in events_path.read_text().splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = row.get("ts", "")
                    cwd = row.get("cwd")
                    where = _os.path.basename(cwd) if cwd else "\u2014"
                    pid_val = row.get("pid")
                    pid = str(pid_val) if pid_val is not None else "\u2014"
                    combined.append((ts, "hook", {
                        "started": _relative_time_cli(ts),
                        "event": row.get("event", "?"),
                        "outcome": row.get("outcome", "?"),
                        "where": where,
                        "pid": pid,
                    }))
            except OSError:
                pass

        if not combined:
            console.print("[dim]No capture activity yet.[/dim]")
            return

        # Sort newest first, limit total.
        combined.sort(key=lambda x: x[0], reverse=True)
        combined = combined[:limit]

        table = Table(title=None)
        table.add_column("ID / Event")
        table.add_column("Type")
        table.add_column("Started")
        table.add_column("Duration")
        table.add_column("Summary")
        table.add_column("Where")
        table.add_column("PID")

        for _ts, kind, data in combined:
            if kind == "run":
                short_id = data["short_id"]  # type: ignore[index]
                if data["schema_mismatch"]:  # type: ignore[index]
                    id_cell = f"[dim]{short_id}[/dim]"
                    summary = f"[dim]{data['summary']} (schema v? \u00b7 upgrade lore)[/dim]"  # type: ignore[index]
                else:
                    id_cell = short_id
                    summary = data["summary"]  # type: ignore[index]
                table.add_row(id_cell, "run", data["started"], data["dur"],  # type: ignore[index]
                              summary, "\u2014", "\u2014")
            else:
                table.add_row(
                    f"[dim]\u2500[/dim]", "[dim]hook[/dim]",
                    f"[dim]{data['started']}[/dim]",  # type: ignore[index]
                    "[dim]\u2014[/dim]",
                    f"[dim]{data['event']} \u00b7 {data['outcome']}[/dim]",  # type: ignore[index]
                    f"[dim]{data['where']}[/dim]",  # type: ignore[index]
                    f"[dim]{data['pid']}[/dim]",  # type: ignore[index]
                )

        console.print(table)
        return

    if not runs_dir.exists() or not any(runs_dir.iterdir()):
        console.print("[dim]No capture activity yet.[/dim]")
        return

    archival = sorted(
        (p for p in runs_dir.glob("*.jsonl") if not p.name.endswith(".trace.jsonl")),
        key=lambda p: p.name,
        reverse=True,
    )[:limit]

    if json_out:
        for p in archival:
            sys.stdout.write(p.read_text())
        return

    table = Table(title=None)
    table.add_column("ID")
    table.add_column("Started")
    table.add_column("Duration")
    table.add_column("Transcripts")
    table.add_column("Notes")
    table.add_column("Reason")
    table.add_column("Errors")

    for p in archival:
        records = read_run(p, strict_schema=False)
        schema_mismatch = any(r.get("_schema_mismatch") for r in records)
        start = next((r for r in records if r.get("type") == "run-start"), {})
        end = next((r for r in reversed(records) if r.get("type") == "run-end"), {})
        short_id = p.stem.split("-")[-1]
        started = _relative_time_cli(start.get("ts", ""))
        dur = f"{end.get('duration_ms', 0) / 1000:.1f}s"
        t_count = sum(1 for r in records if r.get("type") == "transcript-start")
        notes_new = end.get("notes_new", 0)
        notes_merged = end.get("notes_merged", 0)
        skipped = end.get("skipped", 0)
        if notes_new == 0 and notes_merged == 0:
            notes_cell = "0"
            reason = f"all skipped ({skipped})" if skipped else "\u2014"
        else:
            notes_cell = f"{notes_new} new" + (f"+{notes_merged}m" if notes_merged else "")
            reason = "\u2014"
        errors = str(end.get("errors", 0))
        id_cell = short_id
        if schema_mismatch:
            id_cell = f"[dim]{short_id}[/dim]"
            reason = f"[dim]{reason} (schema v? \u00b7 upgrade lore)[/dim]"
        table.add_row(id_cell, started, dur, str(t_count), notes_cell, reason, errors)

    console.print(table)



from lore_core.timefmt import relative_time as _relative_time_cli  # noqa: E402


@app.command("tail")
def tail(
    once: bool = typer.Option(False, "--once", help="Exit on first run-end (don't wait for next run)."),
) -> None:
    """Stream runs-live.jsonl. Default: follow forever. --once: exit on run-end or 30min idle timeout."""
    lore_root = _get_lore_root()
    live = lore_root / ".lore" / "runs-live.jsonl"
    if not live.exists():
        console.print(
            "[dim]No active run. Use `lore runs show latest` "
            "for the last completed run.[/dim]"
        )
        return

    icons = pick_icon_set()
    use_color = should_use_color()
    pos = 0
    idle_since = time.monotonic()
    saw_run_end = False

    while True:
        try:
            size = live.stat().st_size
        except FileNotFoundError:
            console.print("[dim]live log disappeared — exiting.[/dim]")
            return

        # Detect truncation (new run-start truncates runs-live.jsonl).
        if size < pos:
            pos = 0

        if size > pos:
            with live.open("r") as f:
                f.seek(pos)
                chunk = f.read()
                pos = f.tell()
            for line in chunk.splitlines():
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                console.print(render_flat_log([record], icons=icons, use_color=use_color))
                if record.get("type") == "run-end":
                    saw_run_end = True
            idle_since = time.monotonic()

        if once and saw_run_end:
            return
        if once and time.monotonic() - idle_since > _IDLE_TIMEOUT_S:
            console.print(
                "[yellow]no new output for 30min — use `lore runs show <id>` "
                "or check for stale lockfile.[/yellow]"
            )
            return

        time.sleep(_POLL_INTERVAL_S)


@app.command("show")
def show(
    run_id: str = typer.Argument(..., help="latest | ^N | short suffix | full ID | prefix", autocompletion=_complete_run_id),
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
