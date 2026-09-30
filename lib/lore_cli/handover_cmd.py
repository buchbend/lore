"""``lore handover`` — write and read the handover note of a working directory.

  lore handover write < note.md     store the note (stdin), replacing the last one
  lore handover show                print the note

SessionStart injects the note after ``/clear`` or compaction; see
:mod:`lore_core.handover`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from lore_core import handover

app = typer.Typer(
    add_completion=False,
    help="Write and read the handover note that survives /clear and compaction.",
    no_args_is_help=True,
)

_CWD = typer.Option(None, "--cwd", help="Working directory the note belongs to.")


def _cwd(cwd: str | None) -> Path:
    return Path(cwd) if cwd else Path.cwd()


@app.command("write")
def cmd_write(cwd: str = _CWD) -> None:
    """Store the note read from stdin."""
    try:
        path = handover.write(_cwd(cwd), sys.stdin.read())
    except ValueError as exc:
        typer.echo(f"lore handover: {exc}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"handover written: {path}")


@app.command("show")
def cmd_show(cwd: str = _CWD) -> None:
    """Print the note."""
    text = handover.read(_cwd(cwd))
    if text is None:
        typer.echo("lore handover: no note for this directory", err=True)
        raise typer.Exit(code=1)
    typer.echo(text, nl=False)
