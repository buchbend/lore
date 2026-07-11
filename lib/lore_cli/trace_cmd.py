"""`lore trace` — correlated drill-down of one flush.

Reads the event spine + flush record only (see ``lore_core.trace``); never
writes. Consolidates the debugging role of `lore log` / `lore runs` /
`lore proc` into one story keyed by trace_id.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime

import typer
from lore_core.trace import (
    FlushTrace,
    TraceNotFound,
    TraceStep,
    dead_flushes,
    flush_by_trace_id,
    resolve_selector,
)
from rich.console import Console
from rich.tree import Tree

from lore_cli._argv_compat import argv_main

console = Console()

app = typer.Typer(
    add_completion=False,
    help=__doc__,
    no_args_is_help=False,
    rich_markup_mode="rich",
    # Allow options after the positional selector, e.g. `lore trace <id> --plain`
    # as well as `lore trace --plain <id>` (see lore_cli/drill_cmd.py).
    context_settings={"allow_interspersed_args": True},
)


def _parse_ts(raw: str) -> datetime | None:
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _duration_label(steps: list[TraceStep], i: int) -> str:
    """Gap to the *next* step — the trace has no other notion of "how long"."""
    if i + 1 >= len(steps):
        return ""
    a, b = _parse_ts(steps[i].ts), _parse_ts(steps[i + 1].ts)
    if a is None or b is None:
        return ""
    delta = (b - a).total_seconds()
    return f"+{delta:.1f}s" if delta >= 0 else ""


def _step_label(step: TraceStep) -> str:
    bits = [f"{step.source}/{step.event}"]
    if step.event in ("llm-prompt", "llm-response"):
        model = step.data.get("model")
        tokens = step.data.get("token_count")
        if model:
            bits.append(f"model={model}")
        if tokens is not None:
            bits.append(f"tokens={tokens}")
    elif step.event in ("note-filed", "note-appended", "session-note"):
        path = step.data.get("path") or step.data.get("wikilink")
        if path:
            bits.append(f"-> {path}")
    if step.error_code:
        # Parens, not brackets — a literal `[...]` collides with Rich's
        # markup-tag syntax when this label is wrapped in a `[red]...[/red]`.
        bits.append(f"({step.error_code})")
    return " ".join(bits)


def _step_line(steps: list[TraceStep], i: int) -> str:
    step = steps[i]
    line = _step_label(step)
    dur = _duration_label(steps, i)
    return f"{line}  {dur}" if dur else line


def _render_tree(trace: FlushTrace) -> Tree:
    tree = Tree(f"trace {trace.trace_id} — [bold]{trace.status}[/bold]")
    for i, step in enumerate(trace.steps):
        line = _step_line(trace.steps, i)
        if step.level == "error":
            line = f"[red]{line}[/red]"
        elif step.level == "warn":
            line = f"[yellow]{line}[/yellow]"
        tree.add(line)
    return tree


def _render_plain(trace: FlushTrace) -> str:
    lines = [f"trace {trace.trace_id} -- {trace.status}"]
    for i, step in enumerate(trace.steps):
        marker = "!" if step.level == "error" else ("~" if step.level == "warn" else " ")
        lines.append(f"  {marker} {_step_line(trace.steps, i)}")
    if not trace.steps:
        lines.append("  (no spine events for this trace_id)")
    return "\n".join(lines)


def _print_dead(*, records, json_out: bool) -> None:
    if json_out:
        for rec in records:
            sys.stdout.write(json.dumps(rec.to_dict()) + "\n")
        return
    if not records:
        console.print("[dim]No dead-lettered flushes.[/dim]")
        return
    # ponytail: dead-letter listing is a flat table, not a per-flush tree —
    # --plain has nothing extra to strip, so one aligned rendering serves
    # both the default and --plain paths.
    for rec in records:
        line = (
            f"{rec.trace_id or rec.flush_id}  {rec.wiki or '-'}  "
            f"{rec.reason or '-'}  {rec.updated_at}"
        )
        console.print(f"[red]{line}[/red]", highlight=False)


@app.callback(invoke_without_command=True)
def trace(
    # Argument default is None (not `...`) so this callback-only Typer app
    # collapses to a single command instead of a click Group requiring a
    # subcommand after the argument — same workaround as drill_cmd/search_cmd.
    selector: str = typer.Argument(
        None, help="trace-id | session-id | last | dead | note path or [[wikilink]]"
    ),
    plain: bool = typer.Option(False, "--plain", help="Aligned text, no tree glyphs/color."),
    json_out: bool = typer.Option(False, "--json", help="Raw spine event list (JSONL)."),
) -> None:
    """Chronological drill-down of one flush, correlated by trace_id."""
    from lore_core.config import get_lore_root

    if not selector:
        console.print("[red]error:[/red] selector is required")
        raise typer.Exit(code=2)

    try:
        lore_root = get_lore_root()
    except Exception as exc:
        console.print("[red]LORE_ROOT not set.[/red]")
        raise typer.Exit(1) from exc

    try:
        resolved = resolve_selector(lore_root, selector)
    except TraceNotFound as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from e

    if resolved == "dead":
        _print_dead(records=dead_flushes(lore_root), json_out=json_out)
        return

    traces = [flush_by_trace_id(lore_root, tid) for tid in resolved]

    if json_out:
        for t in traces:
            for step in t.steps:
                sys.stdout.write(json.dumps(step.raw) + "\n")
        return

    for t in traces:
        if plain:
            # No markup, no auto-highlight — plain text is meant to be
            # exactly what it says: e.g. literal `(compose-failed)`, no
            # ANSI codes injected around numbers inside trace_ids/models.
            console.print(_render_plain(t), markup=False, highlight=False)
        else:
            console.print(_render_tree(t))


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
