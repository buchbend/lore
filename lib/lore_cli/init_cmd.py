"""`lore init` — scaffold the canonical vault shape at $LORE_ROOT."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import typer
from lore_core.config import get_lore_root
from rich.console import Console

from lore_cli._argv_compat import argv_main

console = Console()

app = typer.Typer(
    add_completion=False,
    help=__doc__,
    no_args_is_help=False,
    rich_markup_mode="rich",
)


def _plugin_templates_dir() -> Path:
    """Backwards-compatible shim for `lore_core.templates.templates_dir`."""
    from lore_core.templates import templates_dir
    return templates_dir()


def _is_interactive() -> bool:
    return sys.stdin.isatty()


def _maybe_set_display_name(root: Path, display_name: str | None) -> None:
    """Onboard the personal display name used in place of `$USER`.

    A `--display-name` flag wins outright (scripted/CI use). Otherwise,
    prompt only when attached to a real terminal and nothing is
    configured yet — reruns (e.g. `--force`) never overwrite an
    existing choice, and non-interactive runs never block on input.
    """
    from lore_core.root_config import load_root_config, set_field

    if display_name:
        set_field(root, "user.display_name", display_name)
        console.print(f"[green]Display name set to {display_name!r}[/green]")
        return
    if load_root_config(root).user.display_name:
        return
    if not _is_interactive():
        return
    name = typer.prompt(
        "Display name (used in session notes and briefings; blank to skip)",
        default="",
        show_default=False,
    )
    if name:
        set_field(root, "user.display_name", name)
        console.print(f"[green]Display name set to {name!r}[/green]")


def init_vault(root: Path, force: bool = False, display_name: str | None = None) -> None:
    """Create the canonical shape at `root`."""
    root.mkdir(parents=True, exist_ok=True)
    _maybe_set_display_name(root, display_name)

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
    display_name: str = typer.Option(
        None,
        "--display-name",
        help="Personal display name for session notes/briefings (skips the prompt).",
    ),
) -> None:
    """Scaffold the canonical vault shape at $LORE_ROOT."""
    target = Path(root).expanduser().resolve() if root else get_lore_root()
    init_vault(target, force=force, display_name=display_name)


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
