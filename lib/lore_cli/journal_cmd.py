"""``lore journal`` — freeform AI + human side-chains.

Two journals at the vault top level:

  $LORE_ROOT/journals/ai.md      — written by the model (volitional)
  $LORE_ROOT/journals/human.md   — written by you (default for the CLI)

Subcommands:
  lore journal write "text"           append (newest-first) — defaults to human
  lore journal write --ai "text"      append to the AI journal
  lore journal read                   show recent entries (default: human)
  lore journal read --ai              show recent AI entries
  lore journal enable | disable       toggle the SessionStart prompt + MCP tools
  lore journal status                 print enabled state + paths

The feature flag gates SessionStart injection and MCP tool exposure;
plain CLI write/read works regardless because nothing about the user
appending to their own scratch pad needs a global toggle.
"""

from __future__ import annotations

import json
import sys
from typing import Optional

import typer

from lore_cli._argv_compat import argv_main
from lore_core import journal

app = typer.Typer(
    add_completion=False,
    help=__doc__,
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _kind_from_flags(ai: bool, human: bool) -> journal.JournalKind:
    if ai and human:
        raise typer.BadParameter("--ai and --human are mutually exclusive")
    return "ai" if ai else "human"


def _emit_json(envelope: dict) -> None:
    print(json.dumps(envelope, indent=2, default=str))


@app.command("write")
def cmd_write(
    text: str = typer.Argument(..., help="Entry text. Wrap in quotes."),
    ai: bool = typer.Option(False, "--ai", help="Write to the AI journal."),
    human: bool = typer.Option(False, "--human", help="Write to the human journal (default)."),
    author: Optional[str] = typer.Option(
        None, "--author", help="Override the auto-resolved author tag."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON envelope."),
) -> None:
    """Append a new entry (newest-first)."""
    kind = _kind_from_flags(ai, human)
    try:
        result = journal.write(kind, text, author=author)
    except ValueError as e:
        typer.echo(f"lore: {e}", err=True)
        raise typer.Exit(code=1) from None
    if json_out:
        _emit_json({"schema": "lore.journal.write/1", "data": result})
    else:
        typer.echo(
            f"{kind} journal · {result['timestamp']} — {result['author']} · {result['path']}"
        )


@app.command("read")
def cmd_read(
    ai: bool = typer.Option(False, "--ai", help="Read the AI journal."),
    human: bool = typer.Option(False, "--human", help="Read the human journal (default)."),
    limit: int = typer.Option(10, "--limit", "-n", help="Max entries to show."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON envelope."),
) -> None:
    """Show recent journal entries (newest first)."""
    kind = _kind_from_flags(ai, human)
    entries = journal.read(kind, limit=limit)
    if json_out:
        _emit_json(
            {
                "schema": "lore.journal.read/1",
                "data": {"kind": kind, "entries": entries},
            }
        )
        return
    if not entries:
        typer.echo(f"({kind} journal is empty — `lore journal write` to start)")
        return
    for entry in entries:
        typer.echo(f"## {entry['timestamp']} — {entry['author']}")
        typer.echo(entry["body"])
        typer.echo("")


@app.command("enable")
def cmd_enable() -> None:
    """Turn on the journal feature flag."""
    path = journal.set_enabled(True)
    typer.echo(f"journal enabled · {path}")


@app.command("disable")
def cmd_disable() -> None:
    """Turn off the journal feature flag."""
    path = journal.set_enabled(False)
    typer.echo(f"journal disabled · {path}")


@app.command("status")
def cmd_status(
    json_out: bool = typer.Option(False, "--json", help="Emit JSON envelope."),
) -> None:
    """Show current state + on-disk paths."""
    state = {
        "enabled": journal.enabled(),
        "ai_path": str(journal.journal_path("ai")),
        "human_path": str(journal.journal_path("human")),
    }
    if json_out:
        _emit_json({"schema": "lore.journal.status/1", "data": state})
        return
    typer.echo(f"enabled: {state['enabled']}")
    typer.echo(f"ai:    {state['ai_path']}")
    typer.echo(f"human: {state['human_path']}")


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
