"""`lore scopes` — inspect and mutate the local scope tree.

Five commands:

* ``lore scopes ls [--tree]``         — list every known scope
* ``lore scopes show SCOPE_ID``       — show one scope + descendants
* ``lore scopes rename OLD NEW``      — rename a scope and all descendants,
                                        propagating ID changes to attachments
* ``lore scopes reparent SCOPE_ID NEW_PARENT`` — move a subtree under a new
                                        parent (leaf segment preserved)
* ``lore scopes rm SCOPE_ID``         — remove a leaf scope (fails if
                                        descendants exist or attachments
                                        still reference it)

Rename and reparent mutate both ``scopes.json`` (via
:class:`~lore_core.state.scopes.ScopesFile`) and ``attachments.json``
(to rewrite ``scope`` values that fell under the renamed subtree).
Both writes happen before any save; on any error the originals remain
untouched because saves are atomic.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from lore_runtime.argv import argv_main
from lore_core.state.attachments import AttachmentsFile
from lore_core.state.scopes import ScopesFile

console = Console()
err_console = Console(stderr=True)

app = typer.Typer(
    add_completion=False,
    help="Inspect and mutate the local scope tree (scopes.json).",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _lore_root_or_die() -> Path:
    env = os.environ.get("LORE_ROOT")
    if not env:
        err_console.print("[red]LORE_ROOT is not set.[/red]")
        raise typer.Exit(1)
    return Path(env)


def _load_scopes() -> ScopesFile:
    sf = ScopesFile(_lore_root_or_die())
    sf.load()
    return sf


def _load_attachments() -> AttachmentsFile:
    af = AttachmentsFile(_lore_root_or_die())
    af.load()
    return af


@app.command("ls")
def cmd_ls(
    tree: bool = typer.Option(False, "--tree", help="Render indented tree by ID depth."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """List every scope."""
    sf = _load_scopes()
    ids = sorted(sf.all_ids())

    if json_out:
        data = {}
        for sid in ids:
            entry = sf.get(sid)
            data[sid] = {
                "label": entry.label,
                "wiki": entry.wiki,
                "description": entry.description,
                "resolved_wiki": sf.resolve_wiki(sid),
            }
        print(json.dumps({"schema": "lore.scopes.ls/1", "data": data}, indent=2))
        return

    if not ids:
        console.print("[yellow]No scopes registered.[/yellow]")
        return

    if tree:
        for sid in ids:
            depth = sid.count(":")
            entry = sf.get(sid)
            indent = "  " * depth
            label = f" — {entry.label}" if entry.label else ""
            wiki = sf.resolve_wiki(sid)
            wiki_str = f" [cyan]({wiki})[/cyan]" if wiki else ""
            console.print(f"{indent}[bold]{sid}[/bold]{wiki_str}{label}")
    else:
        table = Table(title="Scopes")
        table.add_column("id", style="bold")
        table.add_column("wiki (resolved)", style="cyan")
        table.add_column("label")
        for sid in ids:
            entry = sf.get(sid)
            table.add_row(sid, sf.resolve_wiki(sid) or "-", entry.label or "-")
        console.print(table)


@app.command("show")
def cmd_show(
    scope_id: str = typer.Argument(..., help="Scope ID to show."),
) -> None:
    """Show a scope's entry, resolved wiki, and descendants."""
    sf = _load_scopes()
    entry = sf.get(scope_id)
    if entry is None:
        err_console.print(f"[red]No scope {scope_id!r}[/red]")
        raise typer.Exit(1)

    console.print(f"[bold]Scope[/bold] {scope_id}")
    console.print(f"  [cyan]label[/cyan]: {entry.label or '-'}")
    console.print(f"  [cyan]wiki (direct)[/cyan]: {entry.wiki or '-'}")
    console.print(f"  [cyan]wiki (resolved)[/cyan]: {sf.resolve_wiki(scope_id) or '-'}")
    if entry.description:
        console.print(f"  [cyan]description[/cyan]: {entry.description}")

    desc = sf.descendants(scope_id)
    if desc:
        console.print(f"\n[bold]Descendants[/bold] ({len(desc)}):")
        for d in desc:
            console.print(f"  - {d}")

    # Attachments under this scope-subtree
    af = _load_attachments()
    covered = [
        a for a in af.all()
        if a.scope == scope_id or a.scope.startswith(scope_id + ":")
    ]
    if covered:
        console.print(f"\n[bold]Attachments[/bold] ({len(covered)}):")
        for a in covered:
            console.print(f"  - {a.path}  [dim]({a.scope})[/dim]")


@app.command("rename")
def cmd_rename(
    old_id: str = typer.Argument(..., help="Existing scope ID."),
    new_id: str = typer.Argument(..., help="New scope ID."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation."),
) -> None:
    """Rename a scope (and descendants), propagating to attachments."""
    sf = _load_scopes()
    af = _load_attachments()

    if sf.get(old_id) is None:
        err_console.print(f"[red]No scope {old_id!r}[/red]")
        raise typer.Exit(1)

    preview = _rename_preview(sf, af, old_id, new_id)
    _print_rename_preview(preview)

    if not yes and not typer.confirm("Proceed?", default=False):
        console.print("[yellow]Cancelled.[/yellow]")
        raise typer.Exit(0)

    _apply_scope_rewrites(sf, af, preview["scope_rewrites"])
    sf.save()
    af.save()
    console.print("[green]Rename applied.[/green]")


@app.command("reparent")
def cmd_reparent(
    scope_id: str = typer.Argument(..., help="Scope to move."),
    new_parent: str = typer.Argument(..., help="New parent scope ID (empty string for root)."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation."),
) -> None:
    """Move a scope (and descendants) under a new parent.

    The leaf segment is preserved: reparenting ``ccat:data-center`` under
    ``infra`` yields ``infra:data-center``. Pass an empty string for
    ``NEW_PARENT`` to promote the scope to a root.
    """
    sf = _load_scopes()
    af = _load_attachments()

    if sf.get(scope_id) is None:
        err_console.print(f"[red]No scope {scope_id!r}[/red]")
        raise typer.Exit(1)

    leaf = scope_id.rsplit(":", 1)[-1]
    new_id = f"{new_parent}:{leaf}" if new_parent else leaf

    preview = _rename_preview(sf, af, scope_id, new_id)
    _print_rename_preview(preview)

    if not yes and not typer.confirm("Proceed?", default=False):
        console.print("[yellow]Cancelled.[/yellow]")
        raise typer.Exit(0)

    _apply_scope_rewrites(sf, af, preview["scope_rewrites"])
    sf.save()
    af.save()
    console.print("[green]Reparent applied.[/green]")


@app.command("rm")
def cmd_rm(
    scope_id: str = typer.Argument(..., help="Leaf scope ID to remove."),
) -> None:
    """Remove a leaf scope.

    Fails if descendants exist or any attachment still references it.
    """
    sf = _load_scopes()
    af = _load_attachments()

    if sf.get(scope_id) is None:
        err_console.print(f"[red]No scope {scope_id!r}[/red]")
        raise typer.Exit(1)
    if sf.descendants(scope_id):
        err_console.print(
            f"[red]Cannot remove {scope_id!r}: has descendants "
            f"(remove children first or use `rename`).[/red]"
        )
        raise typer.Exit(1)
    blocking = [a for a in af.all() if a.scope == scope_id]
    if blocking:
        err_console.print(
            f"[red]Cannot remove {scope_id!r}: {len(blocking)} attachment(s) "
            f"still reference it.[/red]"
        )
        raise typer.Exit(1)

    sf.remove(scope_id)
    sf.save()
    console.print(f"[green]Removed scope[/green] {scope_id}")


# ---- internals ----

def _rename_preview(sf: ScopesFile, af: AttachmentsFile, old_id: str, new_id: str) -> dict:
    """Dry-run: compute the (old, new) scope rewrites and list affected
    attachments. Does not mutate either file."""
    # Compute the set of IDs that'll be rewritten (the scope + descendants)
    affected = [old_id, *sf.descendants(old_id)]
    rewrites: list[tuple[str, str]] = []
    for sid in affected:
        suffix = sid[len(old_id):]
        rewrites.append((sid, new_id + suffix))
    affected_attachments = [
        a for a in af.all()
        if a.scope == old_id or a.scope.startswith(old_id + ":")
    ]
    return {
        "scope_rewrites": rewrites,
        "affected_attachments": affected_attachments,
    }


def _print_rename_preview(preview: dict) -> None:
    console.print("[bold]Proposed scope rewrites[/bold]:")
    for old, new in preview["scope_rewrites"]:
        console.print(f"  [dim]{old}[/dim] → [bold]{new}[/bold]")
    ats = preview["affected_attachments"]
    if ats:
        console.print(f"\n[bold]Attachments to re-tag[/bold] ({len(ats)}):")
        for a in ats:
            console.print(f"  - {a.path}  [dim]({a.scope})[/dim]")
    else:
        console.print("\n[dim]No attachments reference this subtree.[/dim]")


def _apply_scope_rewrites(
    sf: ScopesFile,
    af: AttachmentsFile,
    rewrites: list[tuple[str, str]],
) -> None:
    """Apply the rewrites to both files (in memory; caller saves)."""
    if not rewrites:
        return
    old_root, new_root = rewrites[0]
    sf.rename(old_root, new_root)
    af.rewrite_scopes(dict(rewrites))


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
