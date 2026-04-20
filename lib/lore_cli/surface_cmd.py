"""`lore surface init` / `add` / `lint` — manage SURFACES.md per wiki."""

from __future__ import annotations

import json
import sys
from importlib import resources
from pathlib import Path

import typer
from rich.console import Console

from lore_core.io import atomic_write_text
from lore_core.surfaces import load_surfaces, SurfaceDef

console = Console()
err_console = Console(stderr=True)

TEMPLATE_NAMES = ("standard", "science", "design", "custom")

app = typer.Typer(
    add_completion=False,
    help=__doc__,
    no_args_is_help=True,
    rich_markup_mode="rich",
)


@app.callback()
def _surface_group(
    ctx: typer.Context,
    wiki: str | None = typer.Option(
        None, "--wiki", help="Wiki name. Inferred from cwd if absent. Shared by `add` and `lint`."
    ),
) -> None:
    """Group-level `--wiki` flag, shared by all subcommands."""
    ctx.ensure_object(dict)
    ctx.obj["wiki"] = wiki


def _resolve_wiki_dir(wiki: str | None) -> Path:
    """Resolve a wiki name to its directory under $LORE_ROOT/wiki/<name>/.

    Raises typer.Exit(1) if wiki is None and the cwd isn't under a wiki dir.
    """
    import os
    lore_root = Path(os.environ.get("LORE_ROOT", Path.home() / "git" / "vault"))
    if wiki:
        return lore_root / "wiki" / wiki
    # Try to infer from cwd: look for wiki/<name>/ ancestor.
    # Match any ancestor whose parent is named "wiki" — that's the wiki
    # root by convention. Don't gate on SURFACES.md existence here; the
    # caller (`add` / `lint`) handles missing-file cases explicitly.
    cwd = Path.cwd().resolve()
    for parent in [cwd, *cwd.parents]:
        if parent.parent.name == "wiki":
            return parent
    err_console.print("[red]could not resolve wiki — pass --wiki <name>[/red]")
    raise typer.Exit(1)


def _load_template(name: str) -> str:
    """Read a shipped template by name."""
    if name not in TEMPLATE_NAMES:
        raise ValueError(f"unknown template {name!r}; choose from {TEMPLATE_NAMES}")
    return resources.files("lore_core.surface_templates").joinpath(f"{name}.md").read_text()


_BARE_HEADER = "# Surfaces\nschema_version: 2\n"


@app.command("init")
def cmd_init(
    ctx: typer.Context,
    wiki: str | None = typer.Option(None, "--wiki", help="Wiki name. Overrides group-level --wiki."),
    template: str = typer.Option(
        "standard", "--template", help=f"Template to seed from: {TEMPLATE_NAMES}"
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite SURFACES.md if it already exists."),
) -> None:
    """Create SURFACES.md from a shipped template. Refuses to overwrite unless --force."""
    if template not in TEMPLATE_NAMES:
        err_console.print(f"[red]unknown template {template!r}; choose from {TEMPLATE_NAMES}[/red]")
        raise typer.Exit(1)
    wiki = wiki or (ctx.obj or {}).get("wiki")
    wiki_dir = _resolve_wiki_dir(wiki)
    surfaces_path = wiki_dir / "SURFACES.md"
    if surfaces_path.exists() and not force:
        err_console.print(f"[red]SURFACES.md already exists at {surfaces_path} (use --force to overwrite)[/red]")
        raise typer.Exit(1)
    wiki_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(surfaces_path, _load_template(template))
    err_console.print(f"[green]initialized {surfaces_path} from template '{template}'[/green]")
    print(json.dumps({"schema": "lore.surface.init/1", "data": {"path": str(surfaces_path), "template": template}}, indent=2))


@app.command("add")
def cmd_add(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Surface name (e.g., 'concept', 'paper')."),
    wiki: str | None = typer.Option(None, "--wiki", help="Wiki name. Overrides group-level --wiki."),
) -> None:
    """Append a new section to SURFACES.md. Creates a minimal file if missing (use `init` to seed from a template)."""
    wiki = wiki or (ctx.obj or {}).get("wiki")
    wiki_dir = _resolve_wiki_dir(wiki)
    surfaces_path = wiki_dir / "SURFACES.md"
    if not surfaces_path.exists():
        wiki_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(surfaces_path, _BARE_HEADER)
    # Reject duplicate
    doc = load_surfaces(wiki_dir)
    if doc is not None and any(s.name == name for s in doc.surfaces):
        err_console.print(f"[red]surface '{name}' already exists in {surfaces_path}[/red]")
        raise typer.Exit(1)
    new_section = f"\n\n## {name}\nTODO: describe this surface.\n\n```yaml\nrequired: [type, created, description, tags]\noptional: [draft]\n```\n"
    text = surfaces_path.read_text()
    if not text.endswith("\n"):
        text += "\n"
    atomic_write_text(surfaces_path, text + new_section)
    err_console.print(f"[green]added surface '{name}' to {surfaces_path}[/green]")
    print(json.dumps({"schema": "lore.surface.add/1", "data": {"path": str(surfaces_path), "name": name}}, indent=2))


@app.command("lint")
def cmd_lint(
    ctx: typer.Context,
    wiki: str | None = typer.Option(None, "--wiki", help="Wiki name. Overrides group-level --wiki."),
) -> None:
    """Validate SURFACES.md: parseable, no duplicate names, each surface has a YAML block."""
    wiki = wiki or (ctx.obj or {}).get("wiki")
    wiki_dir = _resolve_wiki_dir(wiki)
    surfaces_path = wiki_dir / "SURFACES.md"
    if not surfaces_path.exists():
        err_console.print(f"[yellow]no SURFACES.md at {surfaces_path}[/yellow]")
        raise typer.Exit(0)
    issues: list[str] = []
    doc = load_surfaces(wiki_dir)
    if doc is None:
        issues.append("file unparseable")
    else:
        seen: set[str] = set()
        for s in doc.surfaces:
            if s.name in seen:
                issues.append(f"duplicate surface name: {s.name}")
            seen.add(s.name)
            if not s.required:
                issues.append(f"surface '{s.name}' has no `required:` list (no YAML block?)")
    if issues:
        for line in issues:
            err_console.print(f"[red]✗[/red] {line}")
        raise typer.Exit(1)
    err_console.print(f"[green]SURFACES.md OK ({len(doc.surfaces)} surfaces)[/green]")
