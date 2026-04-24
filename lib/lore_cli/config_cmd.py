"""`lore config` — read-only view of resolved configuration."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from lore_core.config import get_lore_root, get_wiki_root
from lore_core.timefmt import relative_time

app = typer.Typer(
    add_completion=False,
    help="Show resolved Lore configuration.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)

console = Console()


def _format_path(p: Path) -> str:
    try:
        home = Path.home()
        if p.is_relative_to(home):
            return "~/" + str(p.relative_to(home))
    except (AttributeError, ValueError):
        pass
    return str(p)


@app.callback(invoke_without_command=True)
def config() -> None:
    """Show resolved Lore configuration."""
    from datetime import UTC, datetime

    try:
        lore_root = get_lore_root()
    except Exception:
        console.print("[red]LORE_ROOT not set.[/red] Run `lore init` or export $LORE_ROOT.")
        raise typer.Exit(1)

    now = datetime.now(UTC)

    console.print(f"  Vault: {_format_path(lore_root)}")

    try:
        wiki_root = get_wiki_root()
        wikis = sorted(d.name for d in wiki_root.iterdir() if d.is_dir()) if wiki_root.exists() else []
    except Exception:
        wikis = []

    if wikis:
        console.print(f"  Wikis: {', '.join(wikis)}")
    else:
        console.print("  Wikis: [dim]none[/dim]")

    console.print()

    from lore_core.state.attachments import AttachmentsFile
    af = AttachmentsFile(lore_root)
    af.load()
    attachments = af.all()

    if attachments:
        console.print(f"  Attachments ({len(attachments)}):")
        for a in attachments:
            rel = relative_time(a.attached_at, now=now)
            console.print(
                f"    {_format_path(a.path):40s} -> {a.wiki}:{a.scope}  "
                f"({a.source}, {rel})"
            )
    else:
        console.print("  Attachments: [dim]none[/dim]")

    console.print()
    console.print("  [dim]Files you edit:[/dim]")
    for wiki in wikis:
        wiki_cfg = lore_root / "wiki" / wiki / ".lore-wiki.yml"
        if wiki_cfg.exists():
            console.print(f"    {_format_path(wiki_cfg):50s} wiki config")
    for a in attachments:
        lore_yml = a.path / ".lore.yml"
        if lore_yml.exists():
            console.print(f"    {_format_path(lore_yml):50s} repo offer")

    console.print()
    console.print("  [dim]Files Lore manages (do not edit):[/dim]")
    for name, desc in [
        ("attachments.json", "attachment state"),
        ("scopes.json", "scope hierarchy"),
        ("transcript-ledger.json", "transcript tracking"),
    ]:
        p = lore_root / ".lore" / name
        if p.exists():
            console.print(f"    {_format_path(p):50s} {desc}")

    console.print()
    console.print("  For health checks: [cyan]lore doctor[/cyan]")
    console.print("  For live activity: [cyan]lore status[/cyan]")
