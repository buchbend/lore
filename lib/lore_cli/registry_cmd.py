"""`lore registry {ls,show,doctor}` — discover and validate wiki registrations.

v1 narrow scope:
  ls      — list wiki dirs under $LORE_ROOT/wiki/
  show    — print the Lore attach block from CLAUDE.md at or above <path>
  doctor  — validate wiki dirs for basic health
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

console = Console()
err_console = Console(stderr=True)

app = typer.Typer(
    add_completion=False,
    help="Registry — discover and validate wiki scopes.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _get_lore_root() -> Path | None:
    lore_root_str = os.environ.get("LORE_ROOT", "")
    if not lore_root_str:
        return None
    return Path(lore_root_str)


def _wiki_dirs(lore_root: Path) -> list[Path]:
    wiki_root = lore_root / "wiki"
    if not wiki_root.exists():
        return []
    return sorted(p for p in wiki_root.iterdir() if p.is_dir())


@app.command("ls")
def registry_ls(
    format_: str = typer.Option("table", "--format", help="Output format: json or table."),
) -> None:
    """List known wiki dirs under $LORE_ROOT/wiki/."""
    lore_root = _get_lore_root()
    if lore_root is None:
        err_console.print("[red]Error:[/red] LORE_ROOT environment variable not set.")
        raise typer.Exit(1)

    dirs = _wiki_dirs(lore_root)
    records = []
    for wiki_dir in dirs:
        scopes_yml = wiki_dir / "_scopes.yml"
        wiki_cfg = wiki_dir / ".lore-wiki.yml"
        records.append({
            "wiki": wiki_dir.name,
            "scopes_yml": "exists" if scopes_yml.exists() else "none",
            "wiki_config": "exists" if wiki_cfg.exists() else "defaults",
        })

    if format_ == "json":
        typer.echo(json.dumps(records, indent=2))
    else:
        table = Table(title="Lore wikis")
        table.add_column("wiki", style="bold")
        table.add_column("scopes_yml")
        table.add_column("wiki_config")
        for rec in records:
            table.add_row(rec["wiki"], rec["scopes_yml"], rec["wiki_config"])
        console.print(table)


@app.command("show")
def registry_show(
    path: str = typer.Argument(..., help="Path to look up in the attachments registry."),
) -> None:
    """Show the attachment (if any) covering ``path`` via longest-prefix match.

    Thin wrapper over ``lore attachments show`` retained for back-compat.
    """
    from lore_core.state.attachments import AttachmentsFile

    lore_root = _get_lore_root()
    if lore_root is None:
        err_console.print("[red]Error:[/red] LORE_ROOT environment variable not set.")
        raise typer.Exit(1)

    search_path = Path(path).resolve()
    af = AttachmentsFile(lore_root)
    af.load()
    match = af.longest_prefix_match(search_path)

    if match is None:
        console.print(f"[yellow]No attachment covers[/yellow] {path}")
        raise typer.Exit(1)

    console.print(f"[bold]Attachment[/bold] from [dim]{match.path}[/dim]")
    console.print(f"  [cyan]wiki[/cyan]: {match.wiki}")
    console.print(f"  [cyan]scope[/cyan]: {match.scope}")
    console.print(f"  [cyan]source[/cyan]: {match.source}")


@app.command("doctor")
def registry_doctor() -> None:
    """Validate wiki dirs for basic health. Exit 1 if issues found."""
    lore_root = _get_lore_root()
    if lore_root is None:
        err_console.print("[red]Error:[/red] LORE_ROOT environment variable not set.")
        raise typer.Exit(1)

    dirs = _wiki_dirs(lore_root)
    issues: list[str] = []

    if not dirs:
        console.print("[yellow]Warning:[/yellow] No wiki directories found under LORE_ROOT/wiki/")
        raise typer.Exit(1)

    for wiki_dir in dirs:
        name = wiki_dir.name
        # Check CLAUDE.md presence
        if not (wiki_dir / "CLAUDE.md").exists():
            issues.append(f"wiki/{name}: missing CLAUDE.md")

        # Check .lore-wiki.yml parseable if present
        wiki_cfg = wiki_dir / ".lore-wiki.yml"
        if wiki_cfg.exists():
            try:
                import yaml
                yaml.safe_load(wiki_cfg.read_text())
            except Exception as exc:
                issues.append(f"wiki/{name}/.lore-wiki.yml: parse error — {exc}")

    if issues:
        console.print("[red]Doctor found issues:[/red]")
        for issue in issues:
            console.print(f"  [yellow]•[/yellow] {issue}")
        raise typer.Exit(1)
    else:
        console.print(f"[green]All {len(dirs)} wiki(s) look healthy.[/green]")
