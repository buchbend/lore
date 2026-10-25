"""`lore glossary` — print the definitions the repo's `CONTEXT.md` holds.

    lore glossary                     # the term index
    lore glossary Ledger              # one definition
    lore glossary "Resume line" Sink  # several in one call

SessionStart injects the term index. An agent prints a definition when it
needs one, instead of reading the whole file. A term the glossary lacks exits
1 and names close terms.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer
from lore_core.glossary import find_repo_root, index_text, load, lookup, suggest

from lore_cli._argv_compat import argv_main

app = typer.Typer(
    add_completion=False,
    help=__doc__,
    no_args_is_help=False,
    rich_markup_mode="rich",
    # A group stops parsing options at its first argument by default.
    context_settings={"allow_interspersed_args": True},
)


@app.callback(invoke_without_command=True)
def glossary(
    terms: Annotated[
        list[str] | None, typer.Argument(help="Terms to define; none prints the index.")
    ] = None,
    cwd: Annotated[
        Path | None, typer.Option("--cwd", help="A folder inside the repo (default: cwd).")
    ] = None,
) -> None:
    """Print the term index, or the definition of each term."""
    root = find_repo_root(cwd or Path.cwd())
    entries = load(root) if root else []
    if not entries:
        print("no glossary: the repo holds no CONTEXT.md or CONTEXT-MAP.md", file=sys.stderr)
        raise typer.Exit(1)
    if not terms:
        print(index_text(entries, root, budget=sys.maxsize))
        return
    missing = False
    blocks: list[str] = []
    for term in terms:
        hits = lookup(entries, term)
        if not hits:
            missing = True
            near = suggest(entries, term)
            hint = f"; close: {', '.join(near)}" if near else ""
            print(f"no term {term!r} in the glossary{hint}", file=sys.stderr)
            continue
        for entry in hits:
            where = entry.path.relative_to(root.resolve())
            blocks.append(f"{where}:{entry.line}\n{entry.text}")
    if blocks:
        print("\n\n".join(blocks))
    if missing:
        raise typer.Exit(1)


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
