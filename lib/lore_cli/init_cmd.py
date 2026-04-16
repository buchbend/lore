"""`lore init` — scaffold the canonical vault shape at $LORE_ROOT."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from rich.console import Console

from lore_core.config import get_lore_root

console = Console()


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
    console.print("  1. Mount a wiki:  [cyan]ln -s <path> {root}/wiki/<name>[/cyan]")
    console.print("     Or scaffold one: [cyan]/lore:new-wiki <name>[/cyan]")
    console.print("  2. Run [cyan]lore lint[/cyan] to seed catalogs.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lore-init", description=__doc__)
    parser.add_argument(
        "--root",
        help="Vault root path (defaults to $LORE_ROOT or ~/lore)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing CLAUDE.md and templates/",
    )
    args = parser.parse_args(argv)
    root = Path(args.root).expanduser().resolve() if args.root else get_lore_root()
    init_vault(root, force=args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
