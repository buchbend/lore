"""``lore quarantine`` — review chapters the publish gate withheld.

The gate holds a withheld chapter's full text in a private sidecar (never
in the shared wiki) and drops a marker in its place in the note. This
command is the reviewer's flow over that sidecar:

  lore quarantine list                 index of withheld chapters (no bodies)
  lore quarantine show <id>            reveal one chapter's full text
  lore quarantine clear <id>           remove one entry after review
  lore quarantine kill <id>            purge one entry
  lore quarantine kill --all           purge every entry

``list`` deliberately prints no body content — an entry may hold the very
secret that tripped the gate. Use ``show`` to reveal a specific one.
"""

from __future__ import annotations

import json
import sys

import typer
from lore_core import quarantine
from lore_core.config import require_lore_root

from lore_cli._argv_compat import argv_main

app = typer.Typer(
    add_completion=False,
    help=__doc__,
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _emit_json(envelope: dict) -> None:
    print(json.dumps(envelope, indent=2, default=str))


@app.command("list")
def cmd_list(
    json_out: bool = typer.Option(False, "--json", help="Emit JSON envelope."),
) -> None:
    """List withheld chapters (metadata only — no bodies)."""
    lore_root = require_lore_root()
    entries = quarantine.list_entries(lore_root=lore_root)
    if json_out:
        _emit_json(
            {
                "schema": "lore.quarantine.list/1",
                "data": [
                    {
                        "id": e.id,
                        "created": e.created,
                        "category": e.category,
                        "note_path": e.note_path,
                        "from_turn": e.from_turn,
                        "to_turn": e.to_turn,
                        "chars": len(e.composed_text),
                    }
                    for e in entries
                ],
            }
        )
        return
    if not entries:
        typer.echo("(quarantine is empty)")
        return
    for e in entries:
        typer.echo(
            f"{e.id}  {e.category:<9}  @{e.from_turn}-{e.to_turn}  "
            f"{len(e.composed_text)} chars  {e.note_path}"
        )
    typer.echo("")
    typer.echo(f"{len(entries)} withheld — `lore quarantine show <id>` to inspect")


@app.command("show")
def cmd_show(
    entry_id: str = typer.Argument(..., help="Quarantine entry id."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON envelope."),
) -> None:
    """Reveal one withheld chapter's full text for review."""
    lore_root = require_lore_root()
    entry = quarantine.get_entry(entry_id, lore_root=lore_root)
    if entry is None:
        typer.echo(f"lore: no quarantine entry {entry_id!r}", err=True)
        raise typer.Exit(code=1)
    if json_out:
        _emit_json(
            {
                "schema": "lore.quarantine.show/1",
                "data": {
                    "id": entry.id,
                    "created": entry.created,
                    "category": entry.category,
                    "note_path": entry.note_path,
                    "from_turn": entry.from_turn,
                    "to_turn": entry.to_turn,
                    "composed_text": entry.composed_text,
                },
            }
        )
        return
    typer.echo(f"id:       {entry.id}")
    typer.echo(f"created:  {entry.created}")
    typer.echo(f"category: {entry.category}")
    typer.echo(f"note:     {entry.note_path}")
    typer.echo(f"turns:    @{entry.from_turn}-{entry.to_turn}")
    typer.echo("--- withheld text ---")
    typer.echo(entry.composed_text)


@app.command("clear")
def cmd_clear(
    entry_id: str = typer.Argument(..., help="Quarantine entry id to remove."),
) -> None:
    """Remove one entry after review."""
    lore_root = require_lore_root()
    if not quarantine.clear_entry(entry_id, lore_root=lore_root):
        typer.echo(f"lore: no quarantine entry {entry_id!r}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"cleared {entry_id}")


@app.command("kill")
def cmd_kill(
    entry_id: str | None = typer.Argument(
        None, help="Entry id to purge (omit with --all to purge everything)."
    ),
    all_: bool = typer.Option(False, "--all", help="Purge every quarantined chapter."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Purge quarantined chapters (one by id, or all with --all)."""
    lore_root = require_lore_root()
    if all_ == (entry_id is not None):
        typer.echo("lore: pass exactly one of <id> or --all", err=True)
        raise typer.Exit(code=2)

    target = "all quarantined chapters" if all_ else f"entry {entry_id}"
    if not yes and not typer.confirm(f"Purge {target}?"):
        raise typer.Abort()

    if all_:
        n = quarantine.kill_all(lore_root=lore_root)
        typer.echo(f"killed {n} entr{'y' if n == 1 else 'ies'}")
        return
    if not quarantine.clear_entry(entry_id, lore_root=lore_root):
        typer.echo(f"lore: no quarantine entry {entry_id!r}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"killed {entry_id}")


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
