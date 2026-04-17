"""`lore search` — run a query against the FTS5 index.

    lore search "transaction buffer"
    lore search "matrix vs slack" --wiki personal
    lore search "retry logic" --for-repo myorg/data-transfer --k 10

Reindex automatically on first run; incremental thereafter via
SHA256 + mtime.
"""

from __future__ import annotations

import argparse
import json
import sys

from rich.console import Console

from lore_search.fts import FtsBackend

console = Console()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lore-search")
    parser.add_argument("query", nargs="?", help="Search query (required unless --reindex or --stats)")
    parser.add_argument("--wiki", help="Scope to one wiki")
    parser.add_argument("--for-repo", dest="for_repo", help="Boost notes tagged with this repo")
    parser.add_argument("--k", type=int, default=5, help="Number of hits to return")
    parser.add_argument("--reindex", action="store_true", help="Full reindex, then exit")
    parser.add_argument("--stats", action="store_true", help="Show index stats and exit")
    parser.add_argument("--json", action="store_true", help="Output hits as JSON")
    args = parser.parse_args(argv)

    backend = FtsBackend()

    if args.stats:
        print(
            json.dumps(
                {"schema": "lore.search.stats/1", "data": backend.stats()},
                indent=2,
            )
        )
        return 0

    # Always refresh incrementally before searching (fast due to SHA256 cache)
    indexed = backend.reindex(wiki=args.wiki)
    if args.reindex:
        console.print(f"Reindexed {indexed} notes")
        return 0

    if not args.query:
        parser.error("query is required (or use --reindex / --stats)")

    hits = backend.search(
        args.query,
        wiki=args.wiki,
        for_repo=args.for_repo,
        k=args.k,
    )

    if args.json:
        print(
            json.dumps(
                {
                    "schema": "lore.search/1",
                    "data": {
                        "query": args.query,
                        "wiki": args.wiki,
                        "for_repo": args.for_repo,
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
        return 0

    if not hits:
        console.print("[yellow]No matches.[/yellow]")
        return 1

    for h in hits:
        console.print(
            f"[bold cyan]{h.wiki}[/bold cyan]/{h.path}  "
            f"[dim]{h.score:.2f}[/dim]"
        )
        if h.description:
            console.print(f"  {h.description}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
