"""`lore surface add` / `lore surface lint` — manage SURFACES.md per wiki."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer
from rich.console import Console

from lore_core.io import atomic_write_text
from lore_core.surfaces import load_surfaces, SurfaceDef

console = Console()
err_console = Console(stderr=True)

_BARE_HEADER = "# Surfaces\nschema_version: 2\n"

app = typer.Typer(
    add_completion=False,
    help=__doc__,
    no_args_is_help=True,
    rich_markup_mode="rich",
)


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


@app.command("init")
def cmd_init(
    ctx: typer.Context,
    wiki: str | None = typer.Option(None, "--wiki", help="Wiki name. Overrides group-level --wiki."),
) -> None:
    """Drop into the /lore:surface-init skill to design the wiki's SURFACES.md set."""
    wiki = wiki or (ctx.obj or {}).get("wiki")
    wiki_dir = _resolve_wiki_dir(wiki)
    wiki_name = wiki_dir.name
    _launch_claude_skill(f"/lore:surface-init {wiki_name}")


@app.command("add")
def cmd_add(
    ctx: typer.Context,
    wiki: str | None = typer.Option(None, "--wiki", help="Wiki name. Overrides group-level --wiki."),
) -> None:
    """Drop into the /lore:surface-new skill to author a new surface interactively."""
    wiki = wiki or (ctx.obj or {}).get("wiki")
    wiki_dir = _resolve_wiki_dir(wiki)
    wiki_name = wiki_dir.name
    _launch_claude_skill(f"/lore:surface-new {wiki_name}")


def _launch_claude_skill(slash_command: str) -> None:
    """Launch the `claude` CLI with a slash command as the initial message."""
    import shutil
    import subprocess
    claude_bin = shutil.which("claude")
    if claude_bin is None:
        err_console.print(
            "[red]`claude` is not on PATH. Install Claude Code "
            "(https://claude.com/code) to use the interactive authoring "
            "flow, or write a draft and call `lore surface commit "
            "<draft.json>` directly.[/red]"
        )
        raise typer.Exit(1)
    try:
        result = subprocess.run([claude_bin, slash_command], check=False)
    except OSError as e:
        err_console.print(f"[red]failed to launch claude: {e}[/red]")
        raise typer.Exit(1)
    if result.returncode != 0:
        raise typer.Exit(result.returncode)


@app.command("commit")
def cmd_commit(
    ctx: typer.Context,
    draft_path: Path = typer.Argument(..., help="Path to the draft.json file."),
    force: bool = typer.Option(
        False,
        "--force",
        help="Bypass duplicate/existing-file checks and write anyway.",
    ),
) -> None:
    """Write a surface draft (append or init) to the target wiki's SURFACES.md."""
    from lore_core.surfaces import (
        SurfaceDef,
        render_section,
        render_document,
        validate_draft,
    )

    if not draft_path.exists():
        err_console.print(f"[red]draft file not found: {draft_path}[/red]")
        raise typer.Exit(1)
    try:
        draft = json.loads(draft_path.read_text())
    except json.JSONDecodeError as e:
        err_console.print(f"[red]draft is not valid JSON: {e}[/red]")
        raise typer.Exit(1)

    wiki = draft.get("wiki")
    if not wiki:
        err_console.print("[red]draft.wiki is required[/red]")
        raise typer.Exit(1)
    wiki_dir = _resolve_wiki_dir(wiki)
    wiki_dir.mkdir(parents=True, exist_ok=True)

    issues = validate_draft(draft, wiki_dir=wiki_dir)
    blocking = [
        i for i in issues
        if not (force and i["code"] in {"duplicate_name", "plural_collision"})
    ]
    if blocking:
        for i in blocking:
            err_console.print(f"[red]✗ {i['code']}[/red]: {i['message']}")
        raise typer.Exit(1)

    surfaces_path = wiki_dir / "SURFACES.md"
    op = draft["operation"]
    if op == "append":
        spec = draft["surface"]
        surface_def = SurfaceDef(
            name=spec["name"],
            description=spec.get("description", ""),
            required=list(spec.get("required") or []),
            optional=list(spec.get("optional") or []),
            extract_when=spec.get("extract_when", ""),
            plural=spec.get("plural"),
            slug_format=spec.get("slug_format"),
            extract_prompt=spec.get("extract_prompt"),
        )
        if not surfaces_path.exists():
            atomic_write_text(surfaces_path, _BARE_HEADER)
        text = surfaces_path.read_text()
        if not text.endswith("\n"):
            text += "\n"
        atomic_write_text(surfaces_path, text + "\n" + render_section(surface_def))
        err_console.print(f"[green]committed surface '{surface_def.name}' to {surfaces_path}[/green]")
        print(json.dumps({
            "schema": "lore.surface.commit/1",
            "data": {"operation": "append", "path": str(surfaces_path), "name": surface_def.name},
        }, indent=2))
    elif op == "init":
        if surfaces_path.exists() and not force:
            err_console.print(
                f"[red]SURFACES.md already exists at {surfaces_path} (use --force to overwrite)[/red]"
            )
            raise typer.Exit(1)
        specs = draft.get("surfaces") or []
        surface_defs = [
            SurfaceDef(
                name=s["name"],
                description=s.get("description", ""),
                required=list(s.get("required") or []),
                optional=list(s.get("optional") or []),
                extract_when=s.get("extract_when", ""),
                plural=s.get("plural"),
                slug_format=s.get("slug_format"),
                extract_prompt=s.get("extract_prompt"),
            )
            for s in specs
        ]
        text = render_document(
            schema_version=draft.get("schema_version", 2),
            surfaces=surface_defs,
            wiki=wiki,
        )
        atomic_write_text(surfaces_path, text)
        err_console.print(
            f"[green]initialized {surfaces_path} with {len(surface_defs)} surface(s)[/green]"
        )
        print(json.dumps({
            "schema": "lore.surface.commit/1",
            "data": {
                "operation": "init",
                "path": str(surfaces_path),
                "surfaces": [s.name for s in surface_defs],
            },
        }, indent=2))
    else:
        err_console.print(f"[red]unknown operation: {op!r}[/red]")
        raise typer.Exit(1)


@app.command("lint")
def cmd_lint(
    wiki: str | None = typer.Option(None, "--wiki", help="Wiki name. Inferred from cwd if absent."),
) -> None:
    """Validate SURFACES.md: parseable, no duplicate names, each surface has a YAML block."""
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
        from lore_core.surfaces import _surface_spec_issues
        seen_names: set[str] = set()
        seen_plurals: set[str] = set()
        for s in doc.surfaces:
            if s.name in seen_names:
                issues.append(f"duplicate surface name: {s.name}")
            seen_names.add(s.name)
            if not s.required:
                issues.append(f"surface '{s.name}' has no `required:` list (no YAML block?)")
            spec = {
                "name": s.name,
                "description": s.description,
                "required": list(s.required),
                "optional": list(s.optional),
                "extract_when": s.extract_when,
                "plural": s.plural,
                "slug_format": s.slug_format,
                "extract_prompt": s.extract_prompt,
            }
            for sub in _surface_spec_issues(
                spec, existing_names=set(), existing_plurals=seen_plurals
            ):
                # Duplicate-name is already handled with a friendlier message above.
                if sub["code"] == "duplicate_name":
                    continue
                issues.append(f"surface '{s.name}': {sub['message']}")
            effective_plural = s.plural or (s.name if s.name.endswith("s") else f"{s.name}s")
            seen_plurals.add(effective_plural)
    if issues:
        for line in issues:
            err_console.print(f"[red]✗[/red] {line}")
        raise typer.Exit(1)
    err_console.print(f"[green]SURFACES.md OK ({len(doc.surfaces)} surfaces)[/green]")
