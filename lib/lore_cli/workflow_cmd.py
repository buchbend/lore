"""`lore workflow` — deterministic epic-workflow substrate (PRD 0003).

Thin Typer wrapper over `lore_workflow`: skills that used to embed this
mechanic as prose now call these subcommands and gate on their exit code.
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from lore_workflow.prd_docs import create_prd
from lore_workflow.roadmap_validator import validate_roadmap
from rich.console import Console

from lore_cli._argv_compat import argv_main

console = Console()

app = typer.Typer(
    add_completion=False,
    help="Deterministic epic-workflow gates: roadmap validation, PRD scaffolding.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


@app.command("validate-roadmap")
def validate_roadmap_cmd(
    path: str = typer.Argument(
        "-", help="Path to the epic body Markdown, or '-' to read stdin."
    ),
) -> None:
    """Validate an epic's roadmap table: required columns, fully-qualified
    `owner/repo#n` issue refs, resolvable blocked-by edges, acyclic DAG.
    """
    text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    result = validate_roadmap(text)
    if result.ok:
        console.print(
            f"[green]roadmap OK[/green]: {len(result.rows)} feature(s), "
            "dependency DAG is acyclic"
        )
        return
    console.print("[red]roadmap INVALID[/red]:")
    for problem in result.problems:
        console.print(f"  - {problem.kind}: {problem.message}")
    raise typer.Exit(code=1)


@app.command("create-prd")
def create_prd_cmd(
    slug: str = typer.Option(..., "--slug", help="Kebab-case PRD slug."),
    title: str = typer.Option(..., "--title", help="PRD title."),
    epic_url: str = typer.Option(..., "--epic-url", help="URL of the tracking epic."),
    repo: list[str] = typer.Option(
        None, "--repo", help="Involved repo (owner/repo). Repeat for multiple."
    ),
    target: Path | None = typer.Option(
        None, "--target", help="Repo root under which docs/prd/ is created (default: cwd)."
    ),
) -> None:
    """Write `docs/prd/NNNN-<slug>.md` and wire it into `docs/prd/index.md`."""
    path = create_prd(
        target or Path("."), slug=slug, title=title, epic_url=epic_url, repos=repo or []
    )
    console.print(f"[green]wrote[/green] {path}")


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
