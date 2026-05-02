"""`lore project` — project orientation note management.

v1 commands (Phase 7):

* ``lore project sync SLUG --to-repo``   — copy orientation's
  ``## Agent guidance`` section to the attached repo's
  ``AGENTS.md`` / ``CLAUDE.md``.
* ``lore project sync SLUG --from-repo`` — copy the attached repo's
  agent file content into the orientation's ``## Agent guidance``
  section.
* ``lore project status SLUG``           — report whether the
  orientation and the repo are in sync.

Future verbs (out of scope for v1):
  * ``lore project orientation`` — open the orientation in $EDITOR.
  * ``lore project list``        — list all project notes in this wiki.
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.console import Console

from lore_cli._argv_compat import argv_main
from lore_core.config import get_wiki_root
from lore_core.projects.agent_sync import (
    compute_sync_status,
    extract_agent_guidance,
    read_repo_agent_file,
    replace_agent_guidance,
    write_repo_agent_file,
)
from lore_core.scope_resolver import resolve_scope
from lore_core.state.attachments import AttachmentsFile

console = Console()
err_console = Console(stderr=True)

app = typer.Typer(
    add_completion=False,
    help="Inspect and reconcile project orientation notes.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _lore_root_or_die() -> Path:
    from lore_cli._cli_helpers import lore_root_or_die
    return lore_root_or_die(err_console)


def _orientation_path_for(slug: str, wiki: str) -> Path | None:
    """Resolve the orientation note path for ``<wiki>/projects/<slug>``.

    Prefers folder-shaped layout ``projects/<slug>/<slug>.md`` and
    falls back to legacy flat ``projects/<slug>.md``.
    """
    wiki_root = get_wiki_root() / wiki
    candidates = [
        wiki_root / "projects" / slug / f"{slug}.md",
        wiki_root / "projects" / f"{slug}.md",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def _resolve_repo_path_for_slug(slug: str) -> Path | None:
    """Find the attached repo path that maps to project ``slug``.

    Walks ``attachments.json`` for an entry whose scope's last segment
    matches ``slug``. Returns the absolute repo path or None.
    """
    af = AttachmentsFile(_lore_root_or_die())
    af.load()
    for a in af.all():
        last = a.scope.rsplit(":", 1)[-1] if a.scope else ""
        if last == slug:
            return a.path
    return None


def _wiki_for_slug(slug: str) -> str | None:
    """Return the wiki name that owns the project ``slug``.

    Looks at attachments first; if multiple, returns the first match.
    """
    af = AttachmentsFile(_lore_root_or_die())
    af.load()
    for a in af.all():
        last = a.scope.rsplit(":", 1)[-1] if a.scope else ""
        if last == slug:
            return a.wiki
    return None


@app.command("status", help="Report orientation/repo sync status for SLUG.")
def cmd_status(slug: str) -> None:
    wiki = _wiki_for_slug(slug)
    if wiki is None:
        err_console.print(
            f"[red]No attached repo found for project slug {slug!r}.[/red]"
        )
        raise typer.Exit(2)

    orientation = _orientation_path_for(slug, wiki)
    if orientation is None:
        err_console.print(
            f"[red]No orientation note found at "
            f"projects/{slug}/{slug}.md or projects/{slug}.md.[/red]"
        )
        raise typer.Exit(2)

    repo_root = _resolve_repo_path_for_slug(slug)
    if repo_root is None:
        err_console.print(
            f"[red]Could not resolve attached repo for slug {slug!r}.[/red]"
        )
        raise typer.Exit(2)

    status = compute_sync_status(orientation, repo_root)

    console.print(f"[bold]project:[/bold] {slug}")
    console.print(f"[bold]wiki:[/bold] {wiki}")
    console.print(f"[bold]orientation:[/bold] {orientation}")
    console.print(f"[bold]repo:[/bold] {repo_root}")
    if not status.orientation_has_section:
        console.print(
            "[yellow]No `## Agent guidance` section in orientation — "
            "nothing to sync.[/yellow]"
        )
        return
    if status.in_sync:
        console.print("[green]In sync.[/green]")
    else:
        console.print(
            "[yellow]Drift detected. Run "
            "`lore project sync " + slug + " --to-repo` (orientation → repo) "
            "or `--from-repo` (repo → orientation) to reconcile.[/yellow]"
        )


@app.command("sync", help="Copy agent guidance between orientation and repo.")
def cmd_sync(
    slug: str,
    to_repo: bool = typer.Option(
        False, "--to-repo", help="Write orientation's `## Agent guidance` to repo's AGENTS.md.",
    ),
    from_repo: bool = typer.Option(
        False, "--from-repo", help="Write repo's AGENTS.md content into orientation's `## Agent guidance`.",
    ),
) -> None:
    if to_repo == from_repo:
        err_console.print(
            "[red]exactly one of --to-repo / --from-repo is required.[/red]"
        )
        raise typer.Exit(2)

    wiki = _wiki_for_slug(slug)
    if wiki is None:
        err_console.print(
            f"[red]No attached repo found for project slug {slug!r}.[/red]"
        )
        raise typer.Exit(2)

    orientation = _orientation_path_for(slug, wiki)
    if orientation is None:
        err_console.print(
            f"[red]No orientation note found for {slug!r}.[/red]"
        )
        raise typer.Exit(2)

    repo_root = _resolve_repo_path_for_slug(slug)
    if repo_root is None:
        err_console.print(
            f"[red]Could not resolve attached repo for {slug!r}.[/red]"
        )
        raise typer.Exit(2)

    if to_repo:
        text = orientation.read_text(errors="replace")
        section = extract_agent_guidance(text)
        if section is None:
            err_console.print(
                "[red]Orientation has no `## Agent guidance` section to copy.[/red]"
            )
            raise typer.Exit(2)
        repo_path, _ = read_repo_agent_file(repo_root)
        if repo_path is None:
            # Default destination: AGENTS.md in the repo root.
            repo_path = repo_root / "AGENTS.md"
        write_repo_agent_file(repo_path, section)
        console.print(
            f"[green]Wrote orientation guidance to {repo_path}[/green]"
        )
        return

    # --from-repo
    repo_path, repo_text = read_repo_agent_file(repo_root)
    if repo_path is None:
        err_console.print(
            "[red]No AGENTS.md or CLAUDE.md found in attached repo.[/red]"
        )
        raise typer.Exit(2)
    text = orientation.read_text(errors="replace")
    new_text = replace_agent_guidance(text, repo_text)
    from lore_core.io import atomic_write_text
    atomic_write_text(orientation, new_text)
    console.print(
        f"[green]Updated orientation `## Agent guidance` from {repo_path}[/green]"
    )


def main() -> None:
    argv_main(app)


if __name__ == "__main__":
    main()
