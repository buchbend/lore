"""`lore codemap` — regenerate the local, gitignored CODEMAP.md.

Deterministic navigation index over one gitignore-aware discovery pass: a
repository inventory plus a ranked Python symbol index. No LLM, no network.
lore's SessionStart hook refreshes it silently; this command is the manual
entry point (and the hook's underlying call).
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from lore_core import codemap

from lore_cli._argv_compat import argv_main

app = typer.Typer(
    add_completion=False,
    help=__doc__,
    no_args_is_help=False,
    rich_markup_mode="rich",
)


@app.callback(invoke_without_command=True)
def codemap_cmd(
    root: str = typer.Argument(None, help="Repository root to map (default: cwd)."),
    quiet: bool = typer.Option(
        False, "--quiet", help="Silent on no-op; swallow errors (for the hook)."
    ),
) -> None:
    """Regenerate CODEMAP.md for the target repository."""
    target = Path(root).expanduser().resolve() if root else Path.cwd()
    result = codemap.generate(target, quiet=quiet)
    if quiet:
        return
    if result.status == "up-to-date":
        typer.echo(f"{codemap.MAP_FILENAME} up to date")
    elif result.status == "created":
        typer.echo(f"{codemap.MAP_FILENAME} created: {len(result.added)} symbols indexed")
    else:
        typer.echo(
            f"{codemap.MAP_FILENAME} updated: +{len(result.added)} / -{len(result.removed)} symbols"
        )


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
