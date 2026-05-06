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
def config(ctx: typer.Context) -> None:
    """Show vault layout (no subcommand) or dispatch to show / get / set / schema."""
    if ctx.invoked_subcommand is not None:
        return
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
    console.print(
        "  For typed config: [cyan]lore config show[/cyan] · "
        "[cyan]get[/cyan] · [cyan]set[/cyan] · [cyan]schema[/cyan]"
    )


def _format_value(v: object) -> str:
    """Render a config value compactly for table display."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str) and v == "":
        return '""'
    return str(v)


@app.command("show")
def cmd_show(
    path_filter: str | None = typer.Argument(
        None,
        help="Optional dotted prefix to filter (e.g. `curator`).",
    ),
    only_overridden: bool = typer.Option(
        False,
        "--changed",
        "-c",
        help="Show only fields whose value differs from the schema default.",
    ),
) -> None:
    """Show the resolved typed configuration with provenance."""
    from rich.table import Table
    from lore_core.root_config import walk_fields

    try:
        lore_root = get_lore_root()
    except Exception:
        console.print("[red]LORE_ROOT not set.[/red] Run `lore init` or export $LORE_ROOT.")
        raise typer.Exit(1)

    rows = walk_fields(lore_root)
    if path_filter:
        rows = [r for r in rows if r.path == path_filter or r.path.startswith(f"{path_filter}.")]
    if only_overridden:
        rows = [r for r in rows if r.value != r.default]
    if not rows:
        console.print("[dim]no matching fields[/dim]")
        return

    cfg_path = lore_root / ".lore" / "config.yml"
    console.print(f"  [dim]Source:[/dim] {_format_path(cfg_path)}")
    console.print()

    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("path", overflow="fold")
    table.add_column("value")
    table.add_column("default")
    table.add_column("from")

    for r in rows:
        value_str = _format_value(r.value)
        default_str = _format_value(r.default)
        if r.value != r.default:
            value_str = f"[yellow]{value_str}[/yellow]"
        source_label = "[green]file[/green]" if r.source == "file" else "[dim]default[/dim]"
        table.add_row(r.path, value_str, default_str, source_label)

    console.print(table)
    console.print()
    console.print(
        "  [dim]Edit one field:[/dim] "
        "[cyan]lore config set <path> <value>[/cyan]"
    )
    console.print(
        "  [dim]Show schema:[/dim]    "
        "[cyan]lore config schema[/cyan]"
    )


@app.command("get")
def cmd_get(
    path: str = typer.Argument(..., help="Dotted config path (e.g. `curator.closure_judgment_enabled`)."),
) -> None:
    """Print one config value."""
    from lore_core.root_config import get_field

    try:
        lore_root = get_lore_root()
    except Exception:
        console.print("[red]LORE_ROOT not set.[/red]")
        raise typer.Exit(1)

    try:
        fi = get_field(lore_root, path)
    except KeyError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(2)
    console.print(_format_value(fi.value))


@app.command("set")
def cmd_set(
    path: str = typer.Argument(..., help="Dotted config path."),
    value: str = typer.Argument(..., help="New value (parsed against the field's declared type)."),
) -> None:
    """Persist a typed value to $LORE_ROOT/.lore/config.yml."""
    from lore_core.root_config import set_field

    try:
        lore_root = get_lore_root()
    except Exception:
        console.print("[red]LORE_ROOT not set.[/red]")
        raise typer.Exit(1)

    try:
        fi = set_field(lore_root, path, value)
    except KeyError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(2)
    except ValueError as e:
        console.print(f"[red]invalid value: {e}[/red]")
        raise typer.Exit(2)
    cfg_path = lore_root / ".lore" / "config.yml"
    console.print(
        f"  [green]✓[/green] {path} = {_format_value(fi.value)}  "
        f"[dim]→ {_format_path(cfg_path)}[/dim]"
    )
    console.print(
        "  [dim]Note: PyYAML round-trip does not preserve inline comments. "
        "Consider re-adding them if the file had any.[/dim]"
    )


@app.command("schema")
def cmd_schema() -> None:
    """Print the full RootConfig schema (paths, types, defaults)."""
    from rich.table import Table
    from lore_core.root_config import schema_tree

    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("path", overflow="fold")
    table.add_column("type")
    table.add_column("default")
    table.add_column("group", overflow="fold")
    for path, type_name, default, group_doc in schema_tree():
        table.add_row(path, type_name, _format_value(default), group_doc[:60])
    console.print(table)
    console.print()
    console.print(
        "  [dim]This is the full set of typed fields stored in[/dim] "
        f"[cyan]$LORE_ROOT/.lore/config.yml[/cyan]."
    )
    console.print(
        "  [dim]Credentials live in[/dim] [cyan]$LORE_ROOT/.lore/secrets.env[/cyan] "
        "[dim](never put feature toggles there).[/dim]"
    )
