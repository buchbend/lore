"""`lore wiki ...` — manage individual wikis under $LORE_ROOT/wiki/.

The canonical home for wiki-lifecycle verbs going forward. Today it
hosts ``new`` (scaffold a new wiki); future work can land alongside
without inventing more top-level CLI verbs.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from enum import Enum
from pathlib import Path

import typer
from lore_core.config import get_wiki_root
from rich.console import Console

from lore_cli._argv_compat import argv_main

console = Console()

app = typer.Typer(
    add_completion=False,
    help=__doc__,
    no_args_is_help=True,
    rich_markup_mode="rich",
)


class WikiMode(str, Enum):
    personal = "personal"
    team = "team"


SUBDIRS = ("projects", "concepts", "decisions", "sessions", "inbox")

# Seeded on wiki creation. Fully commented so `load_scopes_yml` parses it
# to an empty tree — a starting point, never a phantom scope. The shape
# mirrors what `lore_core.scopes.walk_scope_leaves` reads: a top-level
# `scopes:` map whose leaves carry a `repo:` and may nest via `children:`.
_SCOPES_YML_TEMPLATE = """\
# _scopes.yml — declare which repos live under which scope path.
#
# Scopes are colon-separated and hierarchical
# (e.g. ccat:data-center:data-transfer). Each leaf with a `repo:` maps a
# git repo slug (owner/name) onto a scope path; nest deeper with
# `children:`. Uncomment and adapt the example below to get started.
#
# scopes:
#   my-project:
#     repo: your-org/my-project
#     children:
#       backend:
#         repo: your-org/my-project-backend
"""


def _plugin_templates_dir() -> Path:
    from lore_core.templates import templates_dir

    return templates_dir()


def scaffold_wiki(
    name: str,
    *,
    mode: str = "personal",
    remote: str | None = None,
    force: bool = False,
) -> Path:
    wiki_root = get_wiki_root()
    wiki_root.mkdir(parents=True, exist_ok=True)
    target = wiki_root / name

    is_new_wiki = not target.exists()

    if is_new_wiki or force:
        target.mkdir(exist_ok=True)
        for sub in SUBDIRS:
            (target / sub).mkdir(exist_ok=True)

        templates_src = _plugin_templates_dir()
        claude_md = target / "CLAUDE.md"
        claude_md.write_text((templates_src / "wiki-CLAUDE.md").read_text())
        (target / "templates").mkdir(exist_ok=True)
        shutil.copy(templates_src / "session.md", target / "templates" / "session.md")

        # Seed a commented `_scopes.yml` so the scope registry has an
        # obvious home the moment the wiki exists (never overwrite one a
        # cloned/linked wiki already carries).
        scopes_yml = target / "_scopes.yml"
        if not scopes_yml.exists():
            scopes_yml.write_text(_SCOPES_YML_TEMPLATE)

        (target / "_index.txt").write_text(
            f"# {name.upper()} Knowledge Index\n\n"
            f"(Newly created wiki — run `lore lint --wiki {name}` to populate.)\n"
        )

    if mode == "team":
        subprocess.run(["git", "init"], cwd=str(target), check=False)
        if remote:
            subprocess.run(
                ["git", "remote", "add", "origin", remote],
                cwd=str(target),
                check=False,
            )
        subprocess.run(["git", "add", "-A"], cwd=str(target), check=False)
        subprocess.run(
            ["git", "commit", "-m", "lore: initial wiki scaffold"],
            cwd=str(target),
            check=False,
        )

    console.print(f"[green]Created {target}[/green]")
    console.print(f"Next: run [cyan]lore lint --wiki {name}[/cyan] to regenerate catalogs.")
    return target


@app.command("new")
def cmd_new(
    name: str = typer.Argument(..., help="Wiki name (kebab-case)."),
    mode: WikiMode = typer.Option(
        WikiMode.personal,
        "--mode",
        help="`team` mode adds git init + optional remote.",
    ),
    remote: str = typer.Option(None, "--remote", help="Git remote URL (team mode)."),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing wiki directory."),
) -> None:
    """Scaffold a new wiki under $LORE_ROOT/wiki/."""
    scaffold_wiki(name, mode=mode.value, remote=remote, force=force)


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
