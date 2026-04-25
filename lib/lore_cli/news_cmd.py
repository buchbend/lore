"""`lore news` — surface drain events from this session + background work.

Two sources merged:

* The current-session drain file (notes filed / appended on the user's
  behalf while they were talking).
* The ``_system`` drain file (transcript mirror, Curator B surface
  consolidation, future cross-session work).

Events use Lore-internal names (``note-filed``, ``note-appended``, ...)
that are translated into human copy at the display boundary. Machine
vocabulary stays stable; user-visible labels can evolve freely.
"""
from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import typer
from rich.console import Console

from lore_runtime.argv import argv_main
from lore_core.drain import SYSTEM_SESSION, DrainEvent, DrainStore, resolve_session_id

console = Console()
err_console = Console(stderr=True)

app = typer.Typer(
    add_completion=False,
    help="Show what Lore did during this session and in the background.",
    no_args_is_help=False,
    rich_markup_mode="rich",
    invoke_without_command=True,
)


# Human-facing labels for the machine event vocabulary. Display-only.
_COPY = {
    "note-filed": "new note",
    "note-appended": "added to today's note",
    "surface-proposed": "surface proposed",
    "transcript-synced": "transcript synced",
}


def _lore_root_or_die() -> Path:
    from lore_cli._cli_helpers import lore_root_or_die
    return lore_root_or_die(err_console)


def _since_duration(spec: str | None) -> datetime | None:
    """Parse ``--since 10m`` / ``--since 2h`` / ISO datetime. ``None`` → no filter."""
    if not spec:
        return None
    spec = spec.strip()
    now = datetime.now(UTC)
    # Compact units
    if spec and spec[-1] in "smhd" and spec[:-1].isdigit():
        n = int(spec[:-1])
        unit = spec[-1]
        delta = {
            "s": timedelta(seconds=n),
            "m": timedelta(minutes=n),
            "h": timedelta(hours=n),
            "d": timedelta(days=n),
        }[unit]
        return now - delta
    # ISO datetime
    try:
        return datetime.fromisoformat(spec)
    except ValueError:
        err_console.print(f"[yellow]warning: could not parse --since {spec!r}, ignoring[/yellow]")
        return None


def _render_event(e: DrainEvent, *, include_session_tag: str | None = None) -> str:
    label = _COPY.get(e.event, e.event)
    wiki = f" [dim]({e.wiki})[/dim]" if e.wiki else ""
    details = ""
    wikilink = e.data.get("wikilink")
    if wikilink:
        details = f" {wikilink}"
    tag = f" [dim]{include_session_tag}[/dim]" if include_session_tag else ""
    return f"[cyan]·[/cyan] {label}{wiki}{details}{tag}"


def _collect_events(
    lore_root: Path, session_id: str, cutoff: datetime | None, wiki: str | None, limit: int,
) -> tuple[DrainStore, list[DrainEvent], list[DrainEvent]]:
    """Load session + system events since ``cutoff``, filtered by ``wiki``."""
    session_store = DrainStore(lore_root, session_id)
    system_store = DrainStore(lore_root, SYSTEM_SESSION)
    session_events = session_store.read(since=cutoff, limit=limit)
    system_events = system_store.read(since=cutoff, limit=limit)
    if wiki:
        session_events = [e for e in session_events if e.wiki == wiki]
        system_events = [e for e in system_events if e.wiki == wiki]
    return session_store, session_events, system_events


def _advance_cursor(store: DrainStore, events: list[DrainEvent]) -> None:
    """Stamp the store's cursor at the newest ts in ``events``; no-op if empty."""
    if events:
        store.write_cursor(max(e.ts for e in events))


@app.callback()
def cmd_news(
    ctx: typer.Context,
    session: str | None = typer.Option(
        None, "--session", help="Session id; default = current session (best-effort resolve)."
    ),
    wiki: str | None = typer.Option(None, "--wiki", help="Filter to this wiki only."),
    since: str | None = typer.Option(
        None, "--since",
        help="Time window: 10m, 2h, 1d, or ISO datetime. Default: this session's cursor.",
    ),
    limit: int = typer.Option(50, "--limit", help="Max events to display."),
) -> None:
    """Show events from the current session + system work since the cursor."""
    if ctx.invoked_subcommand is not None:
        return  # a subcommand (e.g., `latest`) was invoked — skip default behavior
    lore_root = _lore_root_or_die()

    sid = session or resolve_session_id(Path.cwd())[0]
    cutoff = _since_duration(since)
    if cutoff is None:
        cutoff = DrainStore(lore_root, sid).read_cursor()

    session_store, session_events, system_events = _collect_events(
        lore_root, sid, cutoff, wiki, limit,
    )

    if not session_events and not system_events:
        console.print("[dim]No news.[/dim]")
        return

    if session_events:
        console.print("[bold]This session[/bold]")
        for e in session_events:
            console.print(_render_event(e))

    if system_events:
        if session_events:
            console.print()
        console.print("[bold]Background[/bold]")
        for e in system_events:
            console.print(_render_event(e))

    _advance_cursor(session_store, session_events + system_events)


@app.command("latest", help="Show everything since the last time news was viewed.")
def cmd_latest(
    wiki: str | None = typer.Option(None, "--wiki", help="Filter to this wiki only."),
    limit: int = typer.Option(50, "--limit", help="Max events to display."),
) -> None:
    lore_root = _lore_root_or_die()
    sid, _ = resolve_session_id(Path.cwd())
    cutoff = DrainStore(lore_root, sid).read_cursor()

    session_store, session_events, system_events = _collect_events(
        lore_root, sid, cutoff, wiki, limit,
    )

    if not session_events and not system_events:
        console.print("[dim]Nothing new.[/dim]")
        return

    for e in session_events:
        console.print(_render_event(e, include_session_tag="this session"))
    for e in system_events:
        console.print(_render_event(e, include_session_tag="background"))

    _advance_cursor(session_store, session_events + system_events)


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
