"""Shared CLI helpers — small utilities that more than one ``*_cmd``
module needs but that don't deserve their own module.

Keep this minimal: the test of fitness is "is this duplicated in 3+
files?" If yes, lift it here. If no, leave it inline.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from lore_core.config import (
    LoreRootMissing,
    LoreRootNotSet,
    require_lore_root,
)


def lore_root_or_die(err_console: Console) -> Path:
    """Resolve ``LORE_ROOT``, or render a Rich error and exit 2.

    Single CLI-friendly wrapper around
    :func:`lore_core.config.require_lore_root`. Each command used to
    inline the same 4-line ``env = os.environ.get("LORE_ROOT") ...``
    pattern; this consolidates the rendering so the error wording stays
    consistent across subcommands.
    """
    try:
        return require_lore_root()
    except LoreRootNotSet:
        err_console.print(
            "[red]LORE_ROOT is not set.[/red] "
            "Set $LORE_ROOT to your vault path or run `lore init`."
        )
        raise typer.Exit(code=2)
    except LoreRootMissing as exc:
        err_console.print(f"[red]LORE_ROOT does not exist: {exc.path}[/red]")
        raise typer.Exit(code=2)
