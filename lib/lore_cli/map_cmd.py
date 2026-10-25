"""`lore map` — print the system maps of the wiki, or the edges of one repo.

    lore map                       # every map
    lore map ccatobs/ops-db        # the edges of one repo, from every map
    lore map ccatobs/ops-db --json # the same edges as a JSON list
    lore map check                 # validate the format of every map
    lore map scan --json           # candidate edges from the scope's checkouts

A system map is a wiki note at ``<wiki>/systems/<name>.md`` (PRD 0019, the
system map). SessionStart shows the current repo's neighbours; this command
prints the full list. ``check`` exits 1 on a format error. ``scan`` reads the
checkouts below the scope folder and lists the candidate edges the files
suggest; the ``lore:map`` skill turns them into a wiki PR.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import typer
from lore_core.system_map import edges_for, load_maps, problems, wiki_dirs

from lore_cli._argv_compat import argv_main

app = typer.Typer(
    add_completion=False,
    help=__doc__,
    no_args_is_help=False,
    rich_markup_mode="rich",
    context_settings={"allow_interspersed_args": True},
)


@app.callback(invoke_without_command=True)
def map_(
    target: Annotated[
        str | None,
        typer.Argument(
            help="`owner/repo` for its edges, `check` or `scan`; none prints every map."
        ),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print the edges as JSON.")] = False,
    wiki: Annotated[
        str | None, typer.Option("--wiki", help="Read this wiki only (default: every wiki).")
    ] = None,
    scope: Annotated[
        Path | None,
        typer.Option("--scope", help="`scan`: the scope folder (default: the cwd's scope)."),
    ] = None,
    wiki_dir: Annotated[
        list[Path] | None, typer.Option("--wiki-dir", help="A wiki folder to read.", hidden=True)
    ] = None,
) -> None:
    """Print every map, the edges of one repo, or check the format."""
    maps = load_maps(wiki_dir or wiki_dirs(wiki))
    if target == "scan":
        _scan(scope, maps, as_json)
        return
    if target == "check":
        found = [p for m in maps for p in problems(m)]
        for line in found:
            print(line)
        if found:
            raise typer.Exit(1)
        print(f"{len(maps)} maps, {sum(len(m.edges) for m in maps)} edges: ok")
        return
    if target is not None:
        edges = edges_for(maps, target)
        if as_json:
            print(json.dumps([e.as_dict() for e in edges], indent=2))
            return
        if not edges:
            print(f"no edge names {target} in a system map", file=sys.stderr)
            return
        print("\n".join(f"{e.text()}  ({e.system})" for e in edges))
        return
    if as_json:
        print(json.dumps([e.as_dict() for m in maps for e in m.edges], indent=2))
        return
    if not maps:
        print("no system map: the wiki holds no systems/<name>.md", file=sys.stderr)
        return
    blocks = [
        f"## {m.name} ({m.path})\n" + "\n".join(f"- {e.text()}" for e in m.edges) for m in maps
    ]
    print("\n\n".join(blocks))


def _scan(folder: Path | None, maps: list, as_json: bool) -> None:
    from lore_core import system_map_scan as ms

    name, scope_folder, tidy = ms.scope_for(folder or Path.cwd())
    if folder is None:
        if scope_folder is None:
            print("no scope folder holds the cwd: pass --scope <folder>", file=sys.stderr)
            raise typer.Exit(1)
        folder = scope_folder
    folder = folder.expanduser().resolve()
    checkouts = ms.find_checkouts(folder)
    candidates = ms.scan(folder)
    lacking = ms.missing(candidates, maps)
    if as_json:
        data = {
            "folder": str(folder),
            "scope": name,
            "tidy": tidy,
            "checkouts": {slug: str(path) for slug, path in checkouts.items()},
            "candidates": [c.as_dict() for c in candidates],
            "missing": [c.as_dict() for c in lacking],
            "no_checkout": ms.no_checkout(maps, checkouts),
        }
        print(json.dumps(data, indent=2))
        return
    print(
        f"{len(checkouts)} checkouts in {folder}; {len(lacking)} of "
        f"{len(candidates)} candidates the map lacks"
    )
    for c in lacking:
        print(f"- {c.consumer} <- {c.producer}: {c.kind} {c.detail} ({c.source})")


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
