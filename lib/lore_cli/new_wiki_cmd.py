"""`lore new-wiki <name>` — scaffold a new wiki under $LORE_ROOT/wiki/."""

from __future__ import annotations

import shutil
import subprocess
import sys
from enum import Enum
from importlib import resources
from pathlib import Path

import typer
from lore_core.config import get_wiki_root
from lore_core.io import atomic_write_text
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


class WikiMode(str, Enum):
    personal = "personal"
    team = "team"

SUBDIRS = ("projects", "concepts", "decisions", "sessions", "inbox")
TEMPLATE_NAMES = ("standard", "science", "design", "custom")


def _plugin_templates_dir() -> Path:
    here = Path(__file__).resolve()
    candidates = [
        here.parent.parent.parent / "templates",
        here.parent.parent / "templates",
    ]
    for c in candidates:
        if (c / "wiki-CLAUDE.md").exists():
            return c
    raise FileNotFoundError("Could not locate plugin templates/.")


def _load_template(name: str) -> str:
    """Read a shipped surface template by name."""
    if name not in TEMPLATE_NAMES:
        raise ValueError(f"unknown template {name!r}; choose from {TEMPLATE_NAMES}")
    return resources.files("lore_core.surface_templates").joinpath(f"{name}.md").read_text()


def scaffold_wiki(
    name: str,
    *,
    mode: str = "personal",
    remote: str | None = None,
    force: bool = False,
    surfaces: str = "standard",
) -> Path:
    wiki_root = get_wiki_root()
    wiki_root.mkdir(parents=True, exist_ok=True)
    target = wiki_root / name

    is_new_wiki = not target.exists()

    # Scaffold full wiki structure only if creating new or if force is set
    if is_new_wiki or force:
        target.mkdir(exist_ok=True)
        for sub in SUBDIRS:
            (target / sub).mkdir(exist_ok=True)

        templates_src = _plugin_templates_dir()
        # Copy wiki CLAUDE.md
        claude_md = target / "CLAUDE.md"
        claude_md.write_text((templates_src / "wiki-CLAUDE.md").read_text())
        # Copy the session template so /lore:session can find it
        (target / "templates").mkdir(exist_ok=True)
        shutil.copy(templates_src / "session.md", target / "templates" / "session.md")

        # Seed _index.md so SessionStart doesn't warn on missing catalog
        (target / "_index.md").write_text(
            f"# {name.upper()} Knowledge Index\n\n"
            f"(Newly created wiki — run `lore lint --wiki {name}` to populate.)\n"
        )
    # If wiki already exists and force is False, we skip the full scaffold
    # but still write SURFACES.md below if missing

    # Write SURFACES.md if it doesn't exist
    surfaces_path = target / "SURFACES.md"
    if not surfaces_path.exists():
        if surfaces not in TEMPLATE_NAMES:
            err_console.print(f"[red]unknown template '{surfaces}'; choose from {TEMPLATE_NAMES}[/red]")
            raise typer.Exit(1)
        atomic_write_text(surfaces_path, _load_template(surfaces))

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


@app.callback(invoke_without_command=True)
def new_wiki(
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
        help=f"SURFACES.md template: {TEMPLATE_NAMES}",
    ),
) -> None:
    """Scaffold a new wiki under $LORE_ROOT/wiki/."""
    scaffold_wiki(name, mode=mode.value, remote=remote, force=force, surfaces=surfaces)


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
