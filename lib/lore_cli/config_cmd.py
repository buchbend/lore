"""`lore config` — view and edit resolved configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click
import typer
from lore_core.config import get_lore_root, get_wiki_root
from lore_core.config_schema import ConfigSchema
from lore_core.timefmt import relative_time
from rich.console import Console

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
        raise typer.Exit(1) from None

    now = datetime.now(UTC)

    console.print(f"  Vault: {_format_path(lore_root)}")

    try:
        wiki_root = get_wiki_root()
        wikis = (
            sorted(d.name for d in wiki_root.iterdir() if d.is_dir()) if wiki_root.exists() else []
        )
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
                f"    {_format_path(a.path):40s} -> {a.wiki}:{a.scope}  ({a.source}, {rel})"
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
        "  For typed config: [cyan]lore config show[/cyan] · [cyan]get[/cyan] · "
        "[cyan]set[/cyan] · [cyan]unset[/cyan] · [cyan]edit[/cyan] · [cyan]schema[/cyan]"
    )


def _format_value(v: object) -> str:
    """Render a config value compactly for table display."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str) and v == "":
        return '""'
    return str(v)


def _resolve_target(wiki: str | None) -> tuple[Path, Any, Path, ConfigSchema]:
    """Return (base_dir, module, cfg_path, schema) for root or ``--wiki`` config.

    ``module`` is ``lore_core.root_config`` or ``lore_core.wiki_config`` —
    both expose the same ``get_field``/``set_field``/``unset_field``/
    ``walk_fields`` signatures (thin bindings over ``lore_core.config_schema``).
    """
    if wiki is None:
        from lore_core import root_config as mod

        try:
            lore_root = get_lore_root()
        except Exception:
            console.print("[red]LORE_ROOT not set.[/red] Run `lore init` or export $LORE_ROOT.")
            raise typer.Exit(1) from None
        return lore_root, mod, mod.ROOT_SCHEMA.config_path_fn(lore_root), mod.ROOT_SCHEMA

    from lore_core import wiki_config as mod

    wiki_dir = get_wiki_root() / wiki
    if not wiki_dir.exists():
        console.print(f"[red]wiki not found:[/red] {wiki}")
        raise typer.Exit(1)
    return wiki_dir, mod, mod.WIKI_SCHEMA.config_path_fn(wiki_dir), mod.WIKI_SCHEMA


def _build_resolved_table(rows: list):
    from rich.table import Table

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
    return table


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
    from lore_core.root_config import walk_fields

    try:
        lore_root = get_lore_root()
    except Exception:
        console.print("[red]LORE_ROOT not set.[/red] Run `lore init` or export $LORE_ROOT.")
        raise typer.Exit(1) from None

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
    console.print(_build_resolved_table(rows))
    console.print()
    console.print("  [dim]Edit one field:[/dim] [cyan]lore config set <path> <value>[/cyan]")
    console.print("  [dim]Show schema:[/dim]    [cyan]lore config schema[/cyan]")


_WIKI_OPTION = typer.Option(
    None, "--wiki", help="Target a wiki's .lore-wiki.yml instead of the root config."
)


@app.command("get")
def cmd_get(
    path: str | None = typer.Argument(
        None,
        help="Dotted config path (e.g. `curator.backend`). Omit to show the full "
        "resolved config with provenance.",
    ),
    wiki: str | None = _WIKI_OPTION,
) -> None:
    """Print one config value, or the full resolved config."""
    base_dir, mod, cfg_path, _schema = _resolve_target(wiki)

    if path is None:
        rows = mod.walk_fields(base_dir)
        if not rows:
            console.print("[dim]no matching fields[/dim]")
            return
        console.print(f"  [dim]Source:[/dim] {_format_path(cfg_path)}")
        console.print()
        console.print(_build_resolved_table(rows))
        return

    try:
        fi = mod.get_field(base_dir, path)
    except KeyError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(2) from None
    console.print(_format_value(fi.value))


@app.command("set")
def cmd_set(
    path: str = typer.Argument(..., help="Dotted config path."),
    value: str = typer.Argument(..., help="New value (parsed against the field's declared type)."),
    wiki: str | None = _WIKI_OPTION,
) -> None:
    """Persist a typed value to the target config file.

    Validation happens before any write — an unknown path or a value that
    doesn't parse as the field's declared type leaves the file untouched.
    """
    base_dir, mod, cfg_path, _schema = _resolve_target(wiki)

    try:
        fi = mod.set_field(base_dir, path, value)
    except KeyError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(2) from None
    except ValueError as e:
        console.print(f"[red]invalid value: {e}[/red]")
        raise typer.Exit(2) from None
    console.print(
        f"  [green]✓[/green] {path} = {_format_value(fi.value)}  "
        f"[dim]→ {_format_path(cfg_path)}[/dim]"
    )
    console.print(
        "  [dim]Note: PyYAML round-trip does not preserve inline comments. "
        "Consider re-adding them if the file had any.[/dim]"
    )


@app.command("unset")
def cmd_unset(
    path: str = typer.Argument(
        ..., help="Dotted config path to remove; the field reverts to its default."
    ),
    wiki: str | None = _WIKI_OPTION,
) -> None:
    """Remove a persisted override so a field reverts to its default."""
    base_dir, mod, cfg_path, _schema = _resolve_target(wiki)

    try:
        fi = mod.unset_field(base_dir, path)
    except KeyError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(2) from None
    console.print(
        f"  [green]✓[/green] {path} → default ({_format_value(fi.value)})  "
        f"[dim]→ {_format_path(cfg_path)}[/dim]"
    )


@app.command("edit")
def cmd_edit(wiki: str | None = _WIKI_OPTION) -> None:
    """Open $EDITOR on the target config file; validates on close.

    Refuses to save an invalid result — offers to re-edit or abort (which
    reverts the file to its pre-edit content).
    """
    import yaml
    from lore_core.config_schema import validate_raw

    _base_dir, _mod, cfg_path, schema = _resolve_target(wiki)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    if not cfg_path.exists():
        cfg_path.write_text("")
    original = cfg_path.read_text()

    while True:
        click.edit(filename=str(cfg_path))
        edited = cfg_path.read_text()
        if edited == original:
            console.print("[dim]no changes[/dim]")
            return

        try:
            raw = yaml.safe_load(edited) or {}
        except yaml.YAMLError as e:
            errors = [f"malformed YAML: {e}"]
        else:
            errors = validate_raw(schema.default_factory, raw)

        if not errors:
            console.print(f"  [green]✓[/green] saved [dim]→ {_format_path(cfg_path)}[/dim]")
            return

        console.print("[red]invalid config:[/red]")
        for err in errors:
            console.print(f"  - {err}")
        if not typer.confirm("Re-edit?", default=True):
            cfg_path.write_text(original)
            console.print("[yellow]reverted[/yellow]")
            raise typer.Exit(2)


@app.command("schema")
def cmd_schema() -> None:
    """Print the full RootConfig schema (paths, types, defaults)."""
    from lore_core.root_config import schema_tree
    from rich.table import Table

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
        "[cyan]$LORE_ROOT/.lore/config.yml[/cyan]."
    )
    console.print(
        "  [dim]Credentials live in[/dim] [cyan]$LORE_ROOT/.lore/secrets.env[/cyan] "
        "[dim](never put feature toggles there).[/dim]"
    )
