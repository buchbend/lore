"""`lore inbox` — classify and archive inbox files.

Two subcommands:
  lore inbox classify     read-only: walk every inbox in the vault,
                          return file list with detected type + routing
                          hint as a JSON envelope
  lore inbox archive PATH move a processed inbox file to .processed/
                          with a date prefix

The skill calls classify via MCP, reads each file (LLM judgment),
composes vault notes (LLM body + Bash write), then runs archive to
move the source out of the inbox.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from lore_runtime.argv import argv_main
from lore_core.inbox import archive, classify

app = typer.Typer(
    add_completion=False,
    help=__doc__,
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _emit_json(envelope: dict) -> None:
    print(json.dumps(envelope, indent=2, default=str))


@app.command("classify")
def cmd_classify() -> None:
    """Walk the inboxes and classify what's in there."""
    result = classify()
    _emit_json({"schema": "lore.inbox.classify/1", "data": result})
    if "error" in result:
        raise typer.Exit(code=1)


@app.command("archive")
def cmd_archive(
    path: str = typer.Argument(..., help="Path to the inbox file to archive."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON envelope."),
) -> None:
    """Move a processed file to `.processed/<YYYY-MM-DD>_<name>`."""
    result = archive(source=Path(path))
    if json_out:
        _emit_json({"schema": "lore.inbox.archive/1", "data": result})
    elif "error" in result:
        print(f"lore: {result['error']}", file=sys.stderr)
    else:
        print(f"archived: {result['archived_to']}")
    if "error" in result:
        raise typer.Exit(code=1)


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
