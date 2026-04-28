"""`lore drill` — composite multi-stage retrieval.

    lore drill "transaction buffer"
    lore drill "retry semantics" --wiki private --k 3 --expand-limit 4
    lore drill "x" --json

Calls the same handler as the `lore_drill` MCP tool: search → read top
hits → expand wikilinks → read expanded set, all in one round-trip.
The trace shows what happened at each stage so retrieval failures stay
debuggable.

See `docs/architecture/lore-drill.md`.
"""

from __future__ import annotations

import json
import sys

import typer
from rich.console import Console
from rich.tree import Tree

from lore_cli._argv_compat import argv_main
from lore_mcp.server import handle_drill

console = Console()

app = typer.Typer(
    add_completion=False,
    help=__doc__,
    no_args_is_help=False,
    rich_markup_mode="rich",
    # Allow options after the positional `query` arg so users can write
    # `lore drill "foo" --json` as well as `lore drill --json "foo"`.
    context_settings={"allow_interspersed_args": True},
)


def _render_trace(trace: list[dict]) -> Tree:
    tree = Tree("[bold]drill trace[/bold]")
    for step in trace:
        stage = step.get("stage", "?")
        elapsed = step.get("elapsed_ms", 0)
        if "skipped" in step:
            tree.add(f"[dim]{stage}[/dim] — skipped ({step['skipped']})")
            continue
        node = tree.add(f"[cyan]{stage}[/cyan] [dim]({elapsed} ms)[/dim]")
        if stage == "search":
            node.add(f"query: {step.get('query', '')!r}  hits: {step.get('hits', 0)}")
        elif stage == "read":
            for path in step.get("paths", []):
                node.add(path)
        elif stage == "expand":
            wikilinks = step.get("wikilinks", [])
            node.add(f"{len(wikilinks)} unique wikilinks: {', '.join(wikilinks)}")
        elif stage == "read_expanded":
            for path in step.get("paths", []):
                node.add(path)
            if "truncated" in step:
                node.add(
                    f"[yellow]truncated +{step['truncated']} more (kept {step['kept']})[/yellow]"
                )
    return tree


@app.callback(invoke_without_command=True)
def drill(
    query: str = typer.Argument(None, help="Search query (required)."),
    wiki: str = typer.Option(None, "--wiki", help="Scope to one wiki."),
    k: int = typer.Option(5, "--k", help="Top-k for the search stage."),
    expand_limit: int = typer.Option(
        5, "--expand-limit", help="Max expanded notes to read."
    ),
    json_out: bool = typer.Option(False, "--json", help="Output trace + result as JSON."),
) -> None:
    """Run a drill query — composite chain in one call."""
    if not query:
        console.print("[red]error:[/red] query is required")
        raise typer.Exit(code=2)
    out = handle_drill(query=query, wiki=wiki, k=k, expand_limit=expand_limit)

    if "error" in out:
        if json_out:
            print(json.dumps(out, indent=2))
        else:
            err = out["error"]
            console.print(f"[red]error[/red] ({err.get('code')}): {err.get('message')}")
            if err.get("next"):
                console.print(f"[dim]→ {err['next']}[/dim]")
        raise typer.Exit(code=1)

    if json_out:
        print(json.dumps({"schema": "lore.drill/1", "data": out}, indent=2))
        return

    console.print(_render_trace(out["trace"]))
    console.print()
    notes = out["result"]["notes"]
    if not notes:
        console.print("[yellow]No notes returned.[/yellow]")
        return
    console.print(f"[bold]{len(notes)} notes:[/bold]")
    for note in notes:
        console.print(f"  • [green]{note['wiki']}/{note['path']}[/green]")


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
