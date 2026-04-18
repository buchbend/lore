"""`lore search` — run a query against the FTS5 index.

    lore search "transaction buffer"
    lore search "matrix vs slack" --wiki personal
    lore search "retry logic" --for-repo myorg/data-transfer --k 10

Reindex automatically on first run; incremental thereafter via
SHA256 + mtime.
"""

from __future__ import annotations

import json
import sys

import typer
from rich.console import Console

from lore_cli._compat import argv_main
from lore_search.fts import FtsBackend

console = Console()

app = typer.Typer(
    add_completion=False,
    help=__doc__,
    no_args_is_help=False,
    rich_markup_mode="rich",
)


@app.callback(invoke_without_command=True)
def search(
    query: str = typer.Argument(
        None, help="Search query (required unless --reindex or --stats)."
    ),
    wiki: str = typer.Option(None, "--wiki", help="Scope to one wiki."),
    for_repo: str = typer.Option(
        None, "--for-repo", help="Boost notes tagged with this repo."
    ),
    k: int = typer.Option(5, "--k", help="Number of hits to return."),
    reindex: bool = typer.Option(False, "--reindex", help="Full reindex, then exit."),
    stats: bool = typer.Option(False, "--stats", help="Show index stats and exit."),
    json_out: bool = typer.Option(False, "--json", help="Output hits as JSON."),
) -> None:
    """Run a query against the FTS5 index."""
    backend = FtsBackend()

    if stats:
        print(
            json.dumps(
                {"schema": "lore.search.stats/1", "data": backend.stats()},
                indent=2,
            )
        )
        return

    # Always refresh incrementally before searching (fast due to SHA256 cache)
    indexed = backend.reindex(wiki=wiki)
    if reindex:
        console.print(f"Reindexed {indexed} notes")
        return

    if not query:
        console.print(
            "[red]error:[/red] query is required (or use --reindex / --stats)"
        )
        raise typer.Exit(code=2)

    hits = backend.search(query, wiki=wiki, for_repo=for_repo, k=k)

    if json_out:
        print(
            json.dumps(
                {
                    "schema": "lore.search/1",
                    "data": {
                        "query": query,
                        "wiki": wiki,
                        "for_repo": for_repo,
                        "hits": [
                            {
                                "path": h.path,
                                "wiki": h.wiki,
                                "filename": h.filename,
                                "score": h.score,
                                "description": h.description,
                                "tags": h.tags,
                            }
                            for h in hits
                        ],
                    },
                },
                indent=2,
            )
        )
        return

    if not hits:
        console.print("[yellow]No matches.[/yellow]")
        raise typer.Exit(code=1)

    for h in hits:
        console.print(
            f"[bold cyan]{h.wiki}[/bold cyan]/{h.path}  "
            f"[dim]{h.score:.2f}[/dim]"
        )
        if h.description:
            console.print(f"  {h.description}")


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
