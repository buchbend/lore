"""`lore log` — write a logbook entry and deliver it to the route's sinks (PRD 0017)."""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from lore_workflow import logbook

app = typer.Typer(
    add_completion=False,
    help="Logbook entries: add one at a finish point, retry undelivered ones, show this month.",
    no_args_is_help=True,
)


@app.command("add")
def add_cmd(
    ref: str = typer.Option(..., "--ref", help="A PR, an issue, an epic or branch@sha."),
    text_file: str = typer.Option(
        ..., "--text-file", help="The entry paragraph, or '-' for stdin."
    ),
) -> None:
    """Check an entry, keep it in the ledger and deliver it to every sink.

    Exits 2 when the entry breaks a content rule. A failed sink stays
    undelivered on the `\\[logbook]` ledger line; `lore log flush` retries it.
    """
    text = sys.stdin.read() if text_file == "-" else Path(text_file).read_text(encoding="utf-8")
    rule = logbook.broken_rule(text)
    if rule:
        print(f"log add: the entry breaks the rule: {rule}", file=sys.stderr)
        raise typer.Exit(code=2)
    try:
        entry, status = logbook.add(text, ref, Path.cwd())
    except ValueError as exc:
        print(f"log add: {exc}", file=sys.stderr)
        raise typer.Exit(code=1) from exc
    _print_status(entry, status)


@app.command("flush")
def flush_cmd() -> None:
    """Retry every undelivered sink in this repo's ledger. Always exits 0."""
    results = logbook.flush(Path.cwd())
    if results is None:
        print("another flush or `lore log add` is running; try again later")
        return
    for entry, status in results:
        _print_status(entry, status)
    if not results:
        print("no undelivered logbook entries")


@app.command("show")
def show_cmd() -> None:
    """Print this month's entries from the repo's `file` sink."""
    from lore_core.route import resolve_route
    from lore_workflow.ledger import utc_date

    month = utc_date()[:7]
    root = resolve_route(Path.cwd()).repo_root
    path = logbook.month_file(root, month) if root else None
    if path is None or not path.is_file():
        print(f"no logbook entries for {month}")
        return
    sys.stdout.write(path.read_text(encoding="utf-8"))


def _print_status(entry: logbook.Entry, status: dict[str, bool]) -> None:
    sinks = ", ".join(f"{n} {'delivered' if ok else 'undelivered'}" for n, ok in status.items())
    print(f"{entry.key}: {sinks or 'no sink'}")
