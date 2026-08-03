"""`lore style show <name>` — print the style document that applies here.

    lore style show issue-register
    lore style show issue-register --wiki ccat

Without ``--wiki`` the wiki comes from the scope attached to the cwd. An
unattached cwd resolves to the packaged default, so the command always
prints something an agent can follow.

See ``lore_core.style`` and ``docs/adr/0006-...``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from lore_core.config import get_wiki_root
from lore_core.scope_resolver import resolve_scope
from lore_core.style import UnknownStyle, resolve_style_path
from rich.console import Console

from lore_cli._argv_compat import argv_main

err_console = Console(stderr=True)

app = typer.Typer(
    add_completion=False,
    help="Resolve the prose style documents agents write against.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _wiki_dir(wiki: str | None) -> Path | None:
    """Directory of the named wiki, or of the wiki attached to the cwd."""
    if wiki is None:
        scope = resolve_scope(Path.cwd())
        if scope is None:
            return None
        wiki = scope.wiki
    return get_wiki_root() / wiki


@app.command("show")
def show(
    name: str = typer.Argument(..., help="Style name, e.g. issue-register."),
    wiki: str = typer.Option(
        None, "--wiki", "-w", help="Wiki whose override wins (default: from cwd)."
    ),
) -> None:
    """Print the resolved style document on stdout."""
    try:
        path = resolve_style_path(name, wiki_dir=_wiki_dir(wiki))
    except UnknownStyle as e:
        err_console.print(f"[red]error:[/red] {e}")
        raise typer.Exit(code=1) from None
    # Plain write, not console.print: the text is markdown a linter and an
    # agent read back verbatim, and Rich would reflow it and eat `[...]`.
    sys.stdout.write(path.read_text())


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
