"""`lore init` — scaffold the canonical vault shape at $LORE_ROOT."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import typer
from lore_core.config import get_lore_root
from rich.console import Console

from lore_cli._compat import argv_main

console = Console()

app = typer.Typer(
    add_completion=False,
    help=__doc__,
    no_args_is_help=False,
    rich_markup_mode="rich",
)


def _plugin_templates_dir() -> Path:
    """Find the plugin's templates/ directory (shipped with the install)."""
    # __file__ is lib/lore_cli/init_cmd.py; templates/ is two levels up
    here = Path(__file__).resolve()
    candidates = [
        here.parent.parent.parent / "templates",  # editable install / source
        here.parent.parent / "templates",  # installed package layout
    ]
    for c in candidates:
        if (c / "root-CLAUDE.md").exists():
            return c
    raise FileNotFoundError(
        "Could not locate plugin templates/. Reinstall Lore."
    )


def init_vault(root: Path, force: bool = False) -> None:
    """Create the canonical shape at `root`."""
    root.mkdir(parents=True, exist_ok=True)

    for subdir in ("sessions", "inbox", "drafts", "wiki"):
        (root / subdir).mkdir(exist_ok=True)

    templates_src = _plugin_templates_dir()
    templates_dst = root / "templates"
    if templates_dst.exists() and not force:
        console.print(
            "[yellow]templates/ already exists — leaving untouched "
            "(use --force to overwrite).[/yellow]"
        )
    else:
        shutil.copytree(templates_src, templates_dst, dirs_exist_ok=True)
        console.print(f"[green]Copied templates/ from {templates_src}[/green]")

    claude_md = root / "CLAUDE.md"
    if claude_md.exists() and not force:
        console.print(
            "[yellow]CLAUDE.md already exists — leaving untouched "
            "(use --force to overwrite).[/yellow]"
        )
    else:
        claude_md.write_text((templates_src / "root-CLAUDE.md").read_text())
        console.print(f"[green]Wrote {claude_md}[/green]")

    console.print()
    console.print(f"[bold]Vault initialized at[/bold] {root}")
    console.print()
    console.print("Next steps:")
    console.print(f"  1. Scaffold a wiki: [cyan]lore new-wiki <name>[/cyan]")
    console.print(f"     Or mount existing: [cyan]ln -s <path> {root}/wiki/<name>[/cyan]")
    console.print("  2. Run [cyan]lore lint[/cyan] to seed catalogs.")


@app.callback(invoke_without_command=True)
def init(
    root: str = typer.Option(
        None, "--root", help="Vault root path (defaults to $LORE_ROOT or ~/lore)"
    ),
    force: bool = typer.Option(
        False, "--force", help="Overwrite existing CLAUDE.md and templates/."
    ),
) -> None:
    """Scaffold the canonical vault shape at $LORE_ROOT."""
    target = Path(root).expanduser().resolve() if root else get_lore_root()
    init_vault(target, force=force)


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
