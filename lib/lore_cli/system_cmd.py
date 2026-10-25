"""`lore system` — git status for every repo of a system, and the system's target.

    lore system status                    # every system of the wiki
    lore system status ccat               # one system
    lore system status ccat --fetch       # fetch each checkout first
    lore system status ccat --branch feat/x --json
    lore system target feat/x ccat        # remember the branch ccat belongs on
    lore system target --clear ccat

A system is a system map, a wiki note at ``<wiki>/systems/<name>.md`` (PRD
0019). The map names repos, never folders. Lore finds each checkout through a
``checkouts`` entry in ``~/.config/lore/config.yml`` or below a scope folder.
``status`` lists the checkouts producers first: branch, local work, ahead and
behind, and the way to the target. It changes nothing; the ``lore:system``
skill brings the system to its target. Only ``--fetch`` reaches the network.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from lore_core.system_map import SystemMap, load_maps, wiki_dirs

from lore_cli._argv_compat import argv_main

app = typer.Typer(
    add_completion=False,
    help=__doc__,
    no_args_is_help=True,
    rich_markup_mode="rich",
)

WikiOpt = Annotated[
    str | None, typer.Option("--wiki", help="Read this wiki only (default: every wiki).")
]
WikiDirOpt = Annotated[
    list[Path] | None, typer.Option("--wiki-dir", help="A wiki folder to read.", hidden=True)
]


def _lore_root() -> Path:
    from lore_core.config import get_lore_root

    return get_lore_root()


def _pick(maps: list[SystemMap], name: str | None) -> list[SystemMap]:
    if name is None:
        return maps
    chosen = [m for m in maps if m.name == name]
    if not chosen:
        known = ", ".join(m.name for m in maps) or "none"
        print(f"no system map named {name} (systems: {known})", file=sys.stderr)
        raise typer.Exit(1)
    return chosen


def _home(path: str) -> str:
    home = str(Path.home())
    return "~" + path[len(home) :] if path == home or path.startswith(home + "/") else path


def format_report(data: dict) -> str:
    """The text form of one system's report, as ``lore system status`` prints it."""
    checkouts = data["checkouts"]
    target = data["target"]
    head = data["system"]
    if target:
        set_on = f", set {target['set']}" if target.get("set") else ""
        head += (
            f" · target {target['branch']} ({target['from']}{set_on})"
            f" · {data['at_target']} of {len(checkouts)} checkouts on target"
        )
    else:
        head += f" · no target: `lore system target <branch> {data['system']}`"
    lines = [head]
    width = max(
        [len(c["repo"]) for c in checkouts] + [len(r["repo"]) for r in data["no_checkout"]] + [4]
    )
    rows = []
    for c in checkouts:
        todo = ""
        if c["at_target"] is False:
            todo = "→ settle local work first" if c["action"] == "stop" else f"→ {c['action']}"
        rows.append(
            [
                {True: "✓", False: "✗", None: " "}[c["at_target"]],
                c["repo"],
                c["branch"] or "detached",
                ", ".join(c["local_work"]) or "clean",
                f"↑{c['ahead']} ↓{c['behind']}" if c["ahead"] is not None else "",
                todo,
                _home(c["path"]) + (" (pinned)" if c["source"] == "pinned" else ""),
            ]
        )
    keep = [i for i in range(7) if any(row[i] for row in rows)]
    widths = {i: max(len(row[i]) for row in rows) for i in keep}
    widths[1] = width
    for row in rows:
        cells = [row[i].ljust(widths[i]) if i != keep[-1] else row[i] for i in keep]
        lines.append(("  " + "  ".join(cells)).rstrip())
    for item in data["no_checkout"]:
        lines.append(f"  -  {item['repo'].ljust(width)}  no checkout: {item['hint']}")
    for edge in data["pinned"]:
        lines.append(f"  pin: {edge['consumer']} pins {edge['producer']} by {edge['pin']}")
    for repo in data["fetch_failed"]:
        lines.append(f"  fetch failed: {repo}")
    for warning in data["warnings"]:
        lines.append(f"  warning: {warning}")
    return "\n".join(lines)


@app.command("status")
def status(
    system: Annotated[str | None, typer.Argument(help="The system: its map's note name.")] = None,
    branch: Annotated[
        str | None,
        typer.Option("--branch", help="Compare with this branch instead of the saved target."),
    ] = None,
    do_fetch: Annotated[
        bool, typer.Option("--fetch", help="`git fetch --prune` each checkout first.")
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print the report as JSON.")] = False,
    wiki: WikiOpt = None,
    wiki_dir: WikiDirOpt = None,
) -> None:
    """Show each checkout of a system: branch, local work, and the way to the target."""
    from lore_core.system_checkouts import report

    maps = load_maps(wiki_dir or wiki_dirs(wiki))
    if not maps:
        print("no system map: the wiki holds no systems/<name>.md", file=sys.stderr)
        if as_json:
            print(json.dumps({"systems": []}, indent=2))
        return
    root = _lore_root()
    reports = [report(m, root, branch=branch, do_fetch=do_fetch) for m in _pick(maps, system)]
    if as_json:
        print(json.dumps({"systems": reports}, indent=2))
        return
    print("\n\n".join(format_report(r) for r in reports))


@app.command("target")
def target(
    branch: Annotated[str | None, typer.Argument(help="The branch the system belongs on.")] = None,
    system: Annotated[
        str | None,
        typer.Argument(help="The system: its map's note name. Needed with several maps."),
    ] = None,
    clear: Annotated[bool, typer.Option("--clear", help="Forget the target.")] = False,
    wiki: WikiOpt = None,
    wiki_dir: WikiDirOpt = None,
) -> None:
    """Remember the branch a system belongs on; `lore system status` compares with it."""
    from lore_core.system_checkouts import clear_target, load_target, save_target

    maps = load_maps(wiki_dir or wiki_dirs(wiki))
    if clear and branch and system is None:
        branch, system = None, branch  # `target --clear ccat`
    if system is None:
        if len(maps) != 1:
            names = ", ".join(m.name for m in maps) or "none"
            print(f"name the system (systems: {names})", file=sys.stderr)
            raise typer.Exit(1)
        system = maps[0].name
    _pick(maps, system)
    root = _lore_root()
    if clear:
        print(f"{system}: target cleared" if clear_target(root, system) else f"{system}: no target")
        return
    if branch is None:
        saved = load_target(root, system)
        print(
            f"{system}: target {saved['branch']} (set {saved['set']})"
            if saved
            else f"{system}: no target"
        )
        return
    save_target(root, system, branch, datetime.now(UTC))
    print(f"{system}: target {branch}")


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
