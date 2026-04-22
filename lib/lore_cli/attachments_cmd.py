"""`lore attachments` — inspect and manage the host-local attachments file.

Four commands:

* ``lore attachments ls``      — list every attachment on this host
* ``lore attachments show PATH`` — show the attachment that covers PATH
* ``lore attachments rm PATH``  — remove the attachment at PATH

Underlying state lives at ``$LORE_ROOT/.lore/attachments.json`` and is
mutated through :class:`lore_core.state.attachments.AttachmentsFile`.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from lore_cli._compat import argv_main
from lore_core.state.attachments import Attachment, AttachmentsFile

console = Console()
err_console = Console(stderr=True)

app = typer.Typer(
    add_completion=False,
    help="Inspect and manage host-local attachments (attachments.json).",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _lore_root_or_die() -> Path:
    env = os.environ.get("LORE_ROOT")
    if not env:
        err_console.print("[red]LORE_ROOT is not set.[/red]")
        raise typer.Exit(1)
    return Path(env)


def _load() -> AttachmentsFile:
    af = AttachmentsFile(_lore_root_or_die())
    af.load()
    return af


def _attachment_to_dict(a: Attachment) -> dict:
    return {
        "path": str(a.path),
        "wiki": a.wiki,
        "scope": a.scope,
        "attached_at": a.attached_at.isoformat(),
        "source": a.source,
        "offer_fingerprint": a.offer_fingerprint,
    }


@app.command("ls")
def cmd_ls(
    json_out: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """List every attachment on this host."""
    af = _load()
    entries = af.all()

    if json_out:
        envelope = {
            "schema": "lore.attachments.ls/1",
            "data": [_attachment_to_dict(a) for a in entries],
        }
        print(json.dumps(envelope, indent=2))
        return

    if not entries:
        console.print("[yellow]No attachments registered on this host.[/yellow]")
        return

    table = Table(title="Attachments", show_lines=False)
    table.add_column("path", style="bold")
    table.add_column("wiki", style="cyan")
    table.add_column("scope", style="magenta")
    table.add_column("source")
    for a in sorted(entries, key=lambda a: str(a.path)):
        table.add_row(str(a.path), a.wiki, a.scope, a.source)
    console.print(table)


@app.command("show")
def cmd_show(
    path: str = typer.Argument(..., help="Path to look up (longest-prefix match)."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Show the attachment that covers ``path`` (longest-prefix match).

    Exits 1 if no attachment covers the path.
    """
    af = _load()
    target = Path(path).expanduser()
    match = af.longest_prefix_match(target)

    if match is None:
        if json_out:
            print(json.dumps({"schema": "lore.attachments.show/1", "data": None}, indent=2))
        else:
            console.print(f"[yellow]No attachment covers[/yellow] {target}")
        raise typer.Exit(1)

    if json_out:
        envelope = {
            "schema": "lore.attachments.show/1",
            "data": _attachment_to_dict(match),
        }
        print(json.dumps(envelope, indent=2))
        return

    console.print(f"[bold]Attachment[/bold] covering [dim]{target}[/dim]")
    console.print(f"  [cyan]path[/cyan]: {match.path}")
    console.print(f"  [cyan]wiki[/cyan]: {match.wiki}")
    console.print(f"  [cyan]scope[/cyan]: {match.scope}")
    console.print(f"  [cyan]source[/cyan]: {match.source}")
    if match.offer_fingerprint:
        console.print(f"  [cyan]offer_fingerprint[/cyan]: {match.offer_fingerprint}")


@app.command("rm")
def cmd_rm(
    path: str = typer.Argument(..., help="Exact attachment path to remove."),
) -> None:
    """Remove the attachment at ``path`` (exact match, not prefix).

    Exits 1 if nothing was removed.
    """
    af = _load()
    target = Path(path).expanduser()
    removed = af.remove(target)
    if not removed:
        err_console.print(f"[yellow]No attachment found at[/yellow] {target}")
        raise typer.Exit(1)
    af.save()
    console.print(f"[green]Removed attachment[/green] {target}")


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
