"""`lore trace` — correlated drill-down of one trace_id.

Reads the event spine only (see ``lore_core.trace``); never writes.
Consolidates the debugging role of `lore log` / `lore runs` / `lore proc`
into one story keyed by trace_id.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

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
from rich.markup import escape
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
    elif step.event in ("note-filed", "note-appended"):
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


def _short_model(model: str) -> str:
    return model.removeprefix("claude-")


def _served_label(phase) -> str:
    return ", ".join(_short_model(m) for m in phase.models) or "-"


def _tokens(session: str | None, json_out: bool, days: int) -> None:
    from lore_core.config import get_lore_root
    from lore_core.token_trace import find_transcript, trace_tokens

    try:
        lore_root = get_lore_root()
    except Exception:
        lore_root = None
    if not session:
        _tokens_summary(lore_root, json_out, days)
        return
    path = find_transcript(lore_root, session)
    if path is None:
        console.print(f"[red]no transcript found for session {escape(repr(session))}[/red]")
        raise typer.Exit(code=1)

    traced = trace_tokens(path)
    phases = [p.as_dict() for p in traced]
    total = sum(p["total"] for p in phases)
    if json_out:
        sys.stdout.write(json.dumps({"session": session, "phases": phases, "total": total}) + "\n")
        return
    from rich.table import Table

    table = Table(title=f"tokens {Path(session).name}", box=None, pad_edge=False)
    # A transcript that names no model (an old one) gets no model column.
    show_model = any(m != "?" for phase in traced for m in phase.models)
    # Numbers stay whole; a long phase name folds instead.
    table.add_column("phase", overflow="fold", min_width=8)
    if show_model:
        table.add_column("model", overflow="fold", min_width=5)
    for col in ("messages", "input", "output", "cache read", "cache write", "total"):
        table.add_column(col, justify="right", no_wrap=True, min_width=len(col))
    for phase, p in zip(traced, phases, strict=True):
        table.add_row(
            p["name"],
            *([_served_label(phase)] if show_model else []),
            *(
                str(p[k])
                for k in ("messages", "input", "output", "cache_read", "cache_write", "total")
            ),
        )
    blanks = [""] * (6 if show_model else 5)
    table.add_row("all", *blanks, str(total), style="bold")
    console.print(table, markup=False, highlight=False)
    for phase in traced:
        if phase.model_mismatch:
            console.print(
                f"{phase.name} asked {phase.requested_model}, ran on {_served_label(phase)}",
                markup=False,
                highlight=False,
            )


def _summary_table(title: str, buckets: dict, total_cost: float):
    from rich.table import Table

    table = Table(title=title, box=None, pad_edge=False, title_justify="left")
    table.add_column("", overflow="fold", min_width=8)
    for col in ("input", "output", "cache read", "cache write", "total", "est. USD", "share"):
        table.add_column(col, justify="right", no_wrap=True, min_width=len(col))
    ranked = sorted(buckets.items(), key=lambda kv: (-kv[1].cost, -kv[1].total))
    for name, b in ranked:
        c = b.counts
        share = f"{b.cost / total_cost:.0%}" if total_cost else "-"
        table.add_row(
            _short_model(name),
            *(str(c[k]) for k in ("input", "output", "cache_read", "cache_write")),
            str(b.total),
            f"{b.cost:.2f}" + ("*" if b.unpriced else ""),
            share,
        )
    return table


def _tokens_summary(lore_root: Path | None, json_out: bool, days: int) -> None:
    """Totals over the token records the SessionEnd hook writes."""
    from datetime import UTC, datetime, timedelta

    from lore_core.root_config import load_root_config
    from lore_core.token_prices import PRICES_AS_OF
    from lore_core.token_usage import load_records, summarize

    if lore_root is None:
        console.print("[red]LORE_ROOT not set.[/red] The token records live in the vault.")
        raise typer.Exit(code=1)
    since = datetime.now(UTC) - timedelta(days=days) if days > 0 else None
    prices = load_root_config(lore_root).tokens.prices
    summary = summarize(load_records(lore_root), since=since, prices=prices)
    if json_out:
        data = {"days": days, "prices_as_of": PRICES_AS_OF, **summary.as_dict()}
        sys.stdout.write(json.dumps(data) + "\n")
        return
    span = f"last {days} days" if days > 0 else "all recorded sessions"
    if not summary.sessions:
        console.print(
            f"no sessions recorded ({span}). The SessionEnd hook records each session as it ends.",
            markup=False,
        )
        return
    cost = summary.total.cost
    noun = "session" if summary.sessions == 1 else "sessions"
    console.print(
        f"{summary.sessions} {noun}, {span}: {summary.total.total} tokens, "
        f"about ${cost:.2f} at API list prices ({PRICES_AS_OF})",
        markup=False,
        highlight=False,
    )
    for title, buckets in (
        ("by model", summary.by_model),
        ("by stage", summary.by_stage),
        ("by epic or issue", summary.by_target),
        ("subagents by stage and requested model", summary.by_request),
    ):
        if buckets:
            console.print()
            console.print(_summary_table(title, buckets, cost), markup=False, highlight=False)
    if summary.unpriced_models:
        console.print(
            f"\n* no price for: {', '.join(sorted(summary.unpriced_models))} "
            "(add one under tokens.prices in ~/.config/lore/config.yml)",
            markup=False,
        )
    if summary.mismatches:
        console.print(
            f"\n{len(summary.mismatches)} subagent(s) ran on a different model than requested:",
            markup=False,
        )
        for m in summary.mismatches[:10]:
            console.print(
                f"  {m.session[:8]}  {m.phase}: asked {m.requested}, served {m.served}",
                markup=False,
                highlight=False,
            )


@app.callback(invoke_without_command=True)
def trace(
    # Argument default is None (not `...`) so this callback-only Typer app
    # collapses to a single command instead of a click Group requiring a
    # subcommand after the argument — same workaround as drill_cmd/search_cmd.
    selector: str = typer.Argument(None, help="trace-id | session-id | note path or [[wikilink]]"),
    plain: bool = typer.Option(False, "--plain", help="Aligned text, no tree glyphs/color."),
    json_out: bool = typer.Option(False, "--json", help="Raw spine event list (JSONL)."),
    session: str = typer.Argument(None, help="With `tokens`: session id or .jsonl path."),
    days: int = typer.Option(
        30, "--days", help="With `tokens` and no session: days to sum; 0 for all."
    ),
) -> None:
    """Chronological drill-down of one story, correlated by trace_id.

    `lore trace tokens <session>` prints token totals per skill or agent phase.
    `lore trace tokens` sums the recorded sessions by model, stage, and epic or issue.
    """
    from lore_core.config import get_lore_root

    if not selector:
        console.print("[red]error:[/red] selector is required")
        raise typer.Exit(code=2)

    if selector == "tokens":
        _tokens(session, json_out, days)
        return

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
