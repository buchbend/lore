"""`lore runs` — inspect Curator run logs (reconstructed from the event spine).

Runs no longer have their own JSONL files: curator run events live on the
unified spine (``source="curator"``), grouped by ``run_id``. This command
reads that spine — it is superseded by ``lore trace`` (#192) for drill-down
and slated for deprecation (#195), so it stays a thin history view.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import typer
from lore_core.run_reader import (
    RunIdAmbiguous,
    RunIdNotFound,
    read_run_by_id,
    resolve_run_id,
    run_ids,
)
from lore_core.run_render import (
    pick_icon_set,
    render_flat_log,
    render_summary_panel,
    should_use_color,
)
from rich.console import Console
from rich.panel import Panel

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
err_console = Console(stderr=True)

_DEPRECATION_NOTICE = (
    "[yellow]lore runs is deprecated — use `lore trace` for a correlated "
    "flush drill-down instead. This alias will be removed in a future "
    "release.[/yellow]"
)


@app.callback()
def _deprecation_pointer() -> None:
    """Print the deprecation pointer before any subcommand runs."""
    err_console.print(_DEPRECATION_NOTICE, highlight=False)


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

        suffixes = [rid.split("-")[-1] for rid in run_ids(get_lore_root())]
    except Exception:
        suffixes = []
    candidates = suffixes + ["latest"] + [f"^{i}" for i in range(1, 6)]
    return [c for c in candidates if c.startswith(incomplete)]


def _run_summary_row(run_id: str, records: list[dict]) -> dict:
    """Collapse one run's records into the fields both list views render."""
    start = next((r for r in records if r.get("type") == "run-start"), {})
    end = next((r for r in reversed(records) if r.get("type") == "run-end"), {})
    return {
        "short_id": run_id.split("-")[-1],
        "ts": start.get("ts", ""),
        "dur": f"{end.get('duration_ms', 0) / 1000:.1f}s",
        "transcripts": sum(1 for r in records if r.get("type") == "transcript-start"),
        "notes_new": end.get("notes_new", 0),
        "notes_merged": end.get("notes_merged", 0),
        "skipped": end.get("skipped", 0),
        "errors": end.get("errors", 0),
    }


@app.command("list")
def list_runs(
    limit: int = typer.Option(20, "--limit", help="Maximum runs to show."),
    hooks: bool = typer.Option(False, "--hooks", help="Interleave hook events."),
    json_out: bool = typer.Option(False, "--json", help="Print raw JSONL."),
) -> None:
    """List recent runs (most recent first)."""
    from rich.table import Table

    lore_root = _get_lore_root()
    ids = run_ids(lore_root, limit=limit)

    if hooks:
        import os as _os

        from lore_core.spine import read_spine

        hook_rows = read_spine(lore_root, source="hook")
        has_hook_events = bool(hook_rows)
        has_runs = bool(ids)

        combined: list[tuple[str, str, object]] = []
        for run_id in ids:
            row = _run_summary_row(run_id, read_run_by_id(lore_root, run_id))
            if row["notes_new"] == 0 and row["notes_merged"] == 0:
                summary = f"0 skipped ({row['skipped']})" if row["skipped"] else "0 · 0 errors"
            else:
                summary = f"{row['notes_new']} new" + (
                    f"+{row['notes_merged']}m" if row["notes_merged"] else ""
                )
                summary += f" · {row['errors']} errors"
            combined.append(
                (
                    row["ts"],
                    "run",
                    {
                        "short_id": row["short_id"],
                        "started": _relative_time_cli(row["ts"]),
                        "dur": row["dur"],
                        "summary": summary,
                    },
                )
            )

        for hrow in hook_rows:
            data = hrow.get("data") or {}
            ts = hrow.get("ts", "")
            cwd = data.get("cwd")
            where = _os.path.basename(cwd) if cwd else "—"
            pid_val = data.get("pid")
            pid = str(pid_val) if pid_val is not None else "—"
            combined.append(
                (
                    ts,
                    "hook",
                    {
                        "started": _relative_time_cli(ts),
                        "event": hrow.get("event", "?"),
                        "outcome": data.get("outcome", "?"),
                        "where": where,
                        "pid": pid,
                    },
                )
            )

        if not combined:
            console.print("[dim]No capture activity yet.[/dim]")
            return

        combined.sort(key=lambda x: x[0], reverse=True)
        combined = combined[:limit]

        # Diagnostic banner: runs without hook events means curator ran but
        # Claude Code's capture hook never logged — strong signal that
        # SessionStart isn't invoking `lore hook capture`.
        if has_runs and not has_hook_events:
            console.print(
                "[yellow]! spine has no hook events — SessionStart capture "
                "hook may not be firing.[/yellow]\n"
                "  [dim]Try: lore doctor · check $CLAUDE_PROJECT_DIR · "
                "verify plugin hooks are installed[/dim]"
            )

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
                table.add_row(
                    data["short_id"],
                    "run",
                    data["started"],
                    data["dur"],  # type: ignore[index]
                    data["summary"],
                    "—",
                    "—",
                )  # type: ignore[index]
            else:
                table.add_row(
                    "[dim]─[/dim]",
                    "[dim]hook[/dim]",
                    f"[dim]{data['started']}[/dim]",  # type: ignore[index]
                    "[dim]—[/dim]",
                    f"[dim]{data['event']} · {data['outcome']}[/dim]",  # type: ignore[index]
                    f"[dim]{data['where']}[/dim]",  # type: ignore[index]
                    f"[dim]{data['pid']}[/dim]",  # type: ignore[index]
                )

        console.print(table)
        return

    if not ids:
        console.print("[dim]No capture activity yet.[/dim]")
        return

    if json_out:
        for run_id in ids:
            for rec in read_run_by_id(lore_root, run_id):
                sys.stdout.write(json.dumps({"run_id": run_id, **rec}) + "\n")
        return

    table = Table(title=None)
    table.add_column("ID")
    table.add_column("Started")
    table.add_column("Duration")
    table.add_column("Transcripts")
    table.add_column("Notes")
    table.add_column("Reason")
    table.add_column("Errors")

    for run_id in ids:
        row = _run_summary_row(run_id, read_run_by_id(lore_root, run_id))
        started = _relative_time_cli(row["ts"])
        if row["notes_new"] == 0 and row["notes_merged"] == 0:
            notes_cell = "0"
            reason = f"all skipped ({row['skipped']})" if row["skipped"] else "—"
        else:
            notes_cell = f"{row['notes_new']} new" + (
                f"+{row['notes_merged']}m" if row["notes_merged"] else ""
            )
            reason = "—"
        table.add_row(
            row["short_id"],
            started,
            row["dur"],
            str(row["transcripts"]),
            notes_cell,
            reason,
            str(row["errors"]),
        )

    console.print(table)


from lore_core.timefmt import relative_time as _relative_time_cli  # noqa: E402


@app.command("tail")
def tail(
    once: bool = typer.Option(
        False, "--once", help="Exit on first run-end (don't wait for next run)."
    ),
) -> None:
    """Follow curator events on the spine. Default: forever; --once: exit on run-end."""
    lore_root = _get_lore_root()
    spine = lore_root / ".lore" / "spine.jsonl"
    if not spine.exists():
        console.print(
            "[dim]No active run. Use `lore runs show latest` for the last completed run.[/dim]"
        )
        return

    icons = pick_icon_set()
    use_color = should_use_color()
    pos = 0
    idle_since = time.monotonic()
    saw_run_end = False

    while True:
        try:
            size = spine.stat().st_size
        except FileNotFoundError:
            console.print("[dim]spine disappeared — exiting.[/dim]")
            return

        # Detect rotation (spine.jsonl replaced when it grows too large).
        if size < pos:
            pos = 0

        if size > pos:
            with spine.open("r") as f:
                f.seek(pos)
                chunk = f.read()
                pos = f.tell()
            for line in chunk.splitlines():
                if not line.strip():
                    continue
                try:
                    env = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if env.get("source") != "curator":
                    continue
                rec = {**(env.get("data") or {}), "type": env.get("event"), "ts": env.get("ts")}
                console.print(render_flat_log([rec], icons=icons, use_color=use_color))
                if rec.get("type") == "run-end":
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
    run_id: str = typer.Argument(
        ..., help="latest | ^N | short suffix | full ID | prefix", autocompletion=_complete_run_id
    ),
    verbose: bool = typer.Option(False, "--verbose", help="Include LLM call metadata"),
    raw: bool = typer.Option(
        False, "--raw", help="(retained flag; no effect since trace text is not persisted)"
    ),
    json_out: bool = typer.Option(False, "--json", help="Print raw JSONL"),
) -> None:
    lore_root = _get_lore_root()
    try:
        resolved = resolve_run_id(lore_root, run_id)
    except RunIdNotFound as e:
        console.print(f"[red]Run not found: {e}. Try `lore runs list`.[/red]")
        raise typer.Exit(code=1)
    except RunIdAmbiguous as e:
        console.print(f"[yellow]Ambiguous — matches:[/yellow] {', '.join(e.matches)}")
        raise typer.Exit(code=1)

    records = read_run_by_id(lore_root, resolved)

    if json_out:
        for rec in records:
            sys.stdout.write(json.dumps(rec) + "\n")
        return

    if verbose:
        # Full LLM prompt/response text is no longer persisted (the spine
        # keeps call metadata only); point at the live-trace path instead.
        console.print(
            "[yellow]Full LLM trace text is not persisted. Re-run with "
            "[bold]LORE_TRACE_LLM=1 lore curator run --dry-run[/bold] and watch "
            "the live output for prompts/responses.[/yellow]"
        )

    term_width = shutil.get_terminal_size((80, 20)).columns
    icons = pick_icon_set()
    use_color = should_use_color()

    panel_lines = render_summary_panel(records, term_width=term_width)
    short_id = resolved.split("-")[-1]
    header = f"Run {short_id} ({resolved})"

    if use_color and sys.stdout.isatty():
        console.print(Panel("\n".join(panel_lines), title=header, expand=False))
    else:
        console.print(header)
        for ln in panel_lines:
            console.print(ln)

    flat = render_flat_log(records, icons=icons, use_color=use_color)
    console.print(flat)
