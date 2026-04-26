"""`lore wiki ...` — manage individual wikis under $LORE_ROOT/wiki/.

The canonical home for wiki-lifecycle verbs going forward. Today it
hosts ``new`` (scaffold a new wiki); future work can land alongside
without inventing more top-level CLI verbs.

The legacy ``lore new-wiki <name>`` form is still accepted as an
alias and forwards to the same ``scaffold_wiki()`` implementation.
"""
from __future__ import annotations

from enum import Enum

import typer
from rich.console import Console

from lore_runtime.argv import argv_main

console = Console()
err_console = Console(stderr=True)

app = typer.Typer(
    add_completion=False,
    help=__doc__,
    no_args_is_help=True,
    rich_markup_mode="rich",
)


# Re-import the shared mode enum so callers don't have to thread it
# through both modules.
class WikiMode(str, Enum):
    personal = "personal"
    team = "team"


@app.command("new")
def cmd_new(
    name: str = typer.Argument(..., help="Wiki name (kebab-case)."),
    mode: WikiMode = typer.Option(
        WikiMode.personal,
        "--mode",
        help="`team` mode adds git init + optional remote.",
    ),
    remote: str = typer.Option(
        None, "--remote", help="Git remote URL (team mode)."
    ),
    force: bool = typer.Option(
        False, "--force", help="Overwrite an existing wiki directory."
    ),
    surfaces: str = typer.Option(
        "standard",
        "--surfaces",
        help="SURFACES.md template: standard | science | design | custom",
    ),
) -> None:
    """Scaffold a new wiki under $LORE_ROOT/wiki/."""
    # Lazy import to keep this module's import surface narrow.
    from lore_cli.new_wiki_cmd import scaffold_wiki

    scaffold_wiki(name, mode=mode.value, remote=remote, force=force, surfaces=surfaces)


main = argv_main(app)
