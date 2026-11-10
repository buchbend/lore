"""`lore lab` — write a lab entry and deliver it to the lab sinks (PRD 0020)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer
from lore_workflow import lab

app = typer.Typer(
    add_completion=False,
    help="Lab entries: add one per experiment, show them newest first.",
    no_args_is_help=True,
)


@app.command("add")
def add_cmd(
    text_file: Annotated[str, typer.Option("--text-file", help="The entry text, or '-' for stdin.")],
    cmd: Annotated[str | None, typer.Option("--cmd", help="The command line of the run.")] = None,
    data: Annotated[
        list[str] | None, typer.Option("--data", help="A data path; repeat for more.")
    ] = None,
    thread: Annotated[
        str | None, typer.Option("--thread", help="The slug of the idea this entry follows.")
    ] = None,
    status: Annotated[
        str, typer.Option("--status", help="open, concluded or dead-end.")
    ] = "open",
) -> None:
    """Check an entry, stamp it, keep it in the ledger and deliver it to every lab sink.

    Exits 2 when the entry lacks one of the labels Question, Setup, Result,
    Interpretation and Next, or holds more than 400 words. A failed sink stays
    undelivered on the `\\[lab]` ledger line; `lore log flush` retries it.
    """
    if status not in lab.STATUSES:
        print(f"lab add: --status must be one of {', '.join(lab.STATUSES)}", file=sys.stderr)
        raise typer.Exit(code=2)
    text = sys.stdin.read() if text_file == "-" else Path(text_file).read_text(encoding="utf-8")
    text = text.removeprefix("﻿")
    rule = lab.broken_rule(text)
    if rule:
        print(f"lab add: the entry breaks the rule: {rule}", file=sys.stderr)
        raise typer.Exit(code=2)
    try:
        entry, delivered = lab.add(
            text, Path.cwd(), cmd=cmd, data=data, thread=thread, status=status
        )
    except ValueError as exc:
        print(f"lab add: {exc}", file=sys.stderr)
        raise typer.Exit(code=1) from exc
    sinks = ", ".join(f"{n} {'delivered' if ok else 'undelivered'}" for n, ok in delivered.items())
    print(f"{entry.key}: {sinks or 'no sink'}")


@app.command("show")
def show_cmd(
    thread: Annotated[
        str | None, typer.Option("--thread", help="Only the entries of this thread.")
    ] = None,
) -> None:
    """Print the entries from the repo's `file` sink, newest first."""
    from lore_core.route import resolve_route

    root = resolve_route(Path.cwd()).repo_root
    found = lab.entries(root, thread) if root else []
    if not found:
        print("no lab entries" + (f" in thread {thread}" if thread else ""))
        return
    sys.stdout.write("\n".join(f.read_text(encoding="utf-8") for f, _, _ in found))
