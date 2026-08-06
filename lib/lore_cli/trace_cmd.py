"""`lore trace` — correlated drill-down of one trace_id.

Reads the event spine only (see ``lore_core.trace``); never writes.
Consolidates the debugging role of `lore log` / `lore runs` / `lore proc`
into one story keyed by trace_id.
"""

from __future__ import annotations

import json
import sys

import typer
from lore_core.timefmt import parse_ts
from lore_core.trace import (
    FlushTrace,
    TraceNotFound,
    TraceStep,
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


def _duration_label(steps: list[TraceStep], i: int) -> str:
    """Gap to the *next* step — the trace has no other notion of "how long"."""
    if i + 1 >= len(steps):
        return ""
    a, b = parse_ts(steps[i].ts), parse_ts(steps[i + 1].ts)
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
    tree = Tree(f"trace {trace.trace_id}")
    for i, step in enumerate(trace.steps):
        line = _step_line(trace.steps, i)
        if step.level == "error":
            line = f"[red]{line}[/red]"
        elif step.level == "warn":
            line = f"[yellow]{line}[/yellow]"
        tree.add(line)
    return tree


def _render_plain(trace: FlushTrace) -> str:
    lines = [f"trace {trace.trace_id}"]
    for i, step in enumerate(trace.steps):
        marker = "!" if step.level == "error" else ("~" if step.level == "warn" else " ")
        lines.append(f"  {marker} {_step_line(trace.steps, i)}")
    if not trace.steps:
        lines.append("  (no spine events for this trace_id)")
    return "\n".join(lines)


def _flag_detail(data: dict) -> str:
    """``outcome=...`` for a write, ``verdict=...`` for a review verdict."""
    if "outcome" in data:
        detail = f"outcome={data['outcome']}"
        if data.get("category"):
            detail += f" category={data['category']}"
        return detail
    return f"verdict={data.get('verdict', '?')}"


def _print_flags(*, records: list[dict], json_out: bool) -> None:
    """Flat, chronological listing of flag-write/flag-review spine events.

    Not one correlated tree (flag events carry no trace_id — a flag is a
    standing-alone fact) — a flat table. Review latency for one flag is
    exactly the gap between its ``flag-write`` and ``flag-review`` lines
    here, both keyed by the same ``flag_id``.
    """
    if json_out:
        for rec in records:
            sys.stdout.write(json.dumps(rec) + "\n")
        return
    if not records:
        console.print("[dim]No flag events.[/dim]")
        return
    for rec in records:
        data = rec.get("data") or {}
        line = (
            f"{rec.get('ts', '?')}  {rec.get('event', '?')}  {rec.get('wiki') or '-'}  "
            f"flag_id={data.get('flag_id', '?')}  {_flag_detail(data)}"
        )
        console.print(line, highlight=False)


@app.callback(invoke_without_command=True)
def trace(
    # Argument default is None (not `...`) so this callback-only Typer app
    # collapses to a single command instead of a click Group requiring a
    # subcommand after the argument — same workaround as drill_cmd/search_cmd.
    selector: str = typer.Argument(
        None, help="trace-id | session-id | flag | note path or [[wikilink]]"
    ),
    plain: bool = typer.Option(False, "--plain", help="Aligned text, no tree glyphs/color."),
    json_out: bool = typer.Option(False, "--json", help="Raw spine event list (JSONL)."),
) -> None:
    """Chronological drill-down of one story, correlated by trace_id."""
    from lore_core.config import get_lore_root

    if not selector:
        console.print("[red]error:[/red] selector is required")
        raise typer.Exit(code=2)

    try:
        lore_root = get_lore_root()
    except Exception as exc:
        console.print("[red]LORE_ROOT not set.[/red]")
        raise typer.Exit(1) from exc

    if selector == "flag":
        from lore_core.flag_metrics import flag_events

        _print_flags(records=flag_events(lore_root), json_out=json_out)
        return

    try:
        resolved = resolve_selector(lore_root, selector)
    except TraceNotFound as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from e

    traces = [flush_by_trace_id(lore_root, tid) for tid in resolved]

    if json_out:
        for t in traces:
            for step in t.steps:
                sys.stdout.write(json.dumps(step.raw) + "\n")
        return

    for t in traces:
        if plain:
            # No markup, no auto-highlight — plain text is meant to be
            # exactly what it says: e.g. a literal `(capture-failed)`, no
            # ANSI codes injected around numbers inside trace_ids/models.
            console.print(_render_plain(t), markup=False, highlight=False)
        else:
            console.print(_render_tree(t))


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
