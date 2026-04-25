"""`lore detach` — remove the managed `## Lore` section from CLAUDE.md.

Leaves all content outside the section untouched. No-op if absent.
"""

from __future__ import annotations

import json
import sys

import typer
from rich.console import Console

from lore_runtime.argv import argv_main
from lore_cli.attach_cmd import _resolve_claude_md, remove_section

console = Console()

app = typer.Typer(
    add_completion=False,
    help=__doc__,
    no_args_is_help=False,
    rich_markup_mode="rich",
)


@app.callback(invoke_without_command=True)
def detach(
    path: str = typer.Option(".", "--path", help="Folder or CLAUDE.md path."),
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit a JSON envelope on stdout (lore.detach/1).",
    ),
) -> None:
    """Remove the managed `## Lore` section from CLAUDE.md."""
    target = _resolve_claude_md(path)
    changed = remove_section(target)
    if json_out:
        print(
            json.dumps(
                {
                    "schema": "lore.detach/1",
                    "data": {"path": str(target), "removed": changed},
                },
                indent=2,
            )
        )
    elif changed:
        console.print(f"[green]Detached — removed ## Lore from {target}[/green]")
    else:
        console.print(f"[yellow]No ## Lore section found in {target}[/yellow]")


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
