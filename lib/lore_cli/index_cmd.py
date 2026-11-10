"""`lore index` — print the index of the repo, or keep it in AGENTS.md (PRD 0020).

lore index            # glossary terms, ADRs, current PRDs, roadmap, neighbours
lore index --write    # replace the marked block in AGENTS.md, or append it
lore index --check    # exit 1 when the block in AGENTS.md is stale
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer
from lore_core import repo_index
from lore_core.glossary import find_repo_root

app = typer.Typer(add_completion=False, help=__doc__, rich_markup_mode="rich")


def _neighbours(repo_root: Path) -> list[str]:
    from lore_core.route import resolve_route
    from lore_core.system_map import session_start_block

    try:
        block = session_start_block(repo_root, resolve_route(repo_root).wiki)
    except Exception:  # noqa: BLE001 - no wiki, no map: the group stays out
        return []
    # The block's own lines minus its heading and its count line.
    return [ln for ln in (block or "").split("\n")[1:] if ln.startswith("- ")]


@app.callback(invoke_without_command=True)
def index(
    cwd: Annotated[
        Path | None, typer.Option("--cwd", help="A folder inside the repo (default: cwd).")
    ] = None,
    write: Annotated[bool, typer.Option("--write", help="Write the block into AGENTS.md.")] = False,
    check: Annotated[
        bool, typer.Option("--check", help="Exit 1 when the AGENTS.md block is stale.")
    ] = False,
) -> None:
    """Print the index of the repo."""
    root = find_repo_root(cwd or Path.cwd())
    if root is None:
        print("lore index: not inside a git repository", file=sys.stderr)
        raise typer.Exit(1)
    agents = root / "AGENTS.md"
    try:
        if check:
            if not repo_index.agents_block_is_current(root):
                print(f"lore index: {agents} is stale; run `lore index --write`", file=sys.stderr)
                raise typer.Exit(1)
            print(f"lore index: {agents} is current")
            return
        if write:
            wrote = repo_index.write_agents_block(root)
            print(f"lore index: {'wrote' if wrote else 'unchanged'} {agents}")
            return
    except repo_index.UnmatchedMarker as exc:
        print(f"lore index: {agents}: {exc}; fix the markers by hand", file=sys.stderr)
        raise typer.Exit(1) from None
    text = repo_index.index_text(repo_index.groups(root, _neighbours(root)))
    if text:
        print(text)
