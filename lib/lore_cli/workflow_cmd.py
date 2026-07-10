"""`lore workflow` — deterministic epic-workflow substrate (PRD 0003).

Thin Typer wrapper over `lore_workflow`: skills that used to embed this
mechanic as prose now call these subcommands and gate on their exit code.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

import typer
from lore_workflow.board_parser import BoardParseError, parse_board
from lore_workflow.epic_policy import resolve_epic_policy
from lore_workflow.prd_docs import create_prd
from lore_workflow.roadmap_validator import roadmap_counts, validate_roadmap
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
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit machine output: {ok, rows, repos, edges, problems}. "
        "Exit code is unchanged (0 valid, 1 invalid).",
    ),
) -> None:
    """Validate an epic's roadmap table: required columns, fully-qualified
    `owner/repo#n` issue refs, resolvable blocked-by edges, acyclic DAG.
    """
    text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    result = validate_roadmap(text)
    if as_json:
        counts = roadmap_counts(result)
        # stdout, not rich console: keep it parse-clean (no markup/wrapping).
        print(
            json.dumps(
                {
                    "ok": result.ok,
                    "rows": counts.rows,
                    "repos": counts.repos,
                    "edges": counts.edges,
                    "problems": [
                        {"kind": p.kind, "message": p.message} for p in result.problems
                    ],
                }
            )
        )
        if not result.ok:
            raise typer.Exit(code=1)
        return
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


@app.command("epic-policy")
def epic_policy_cmd(
    repo_root: str = typer.Argument(
        ".", help="Repo root to resolve policy for (default: cwd)."
    ),
) -> None:
    """Emit a repo's epic-merge policy as JSON: {target_branch, deploy_gate}.

    `target_branch` is `develop` if that branch exists on `origin`, else
    `main`. `deploy_gate` is true iff `AGENTS.md` declares
    `epic-merge-policy: confirm` under its `## Epic merge policy` section.
    """
    policy = resolve_epic_policy(Path(repo_root))
    # stdout, not rich console: keep it parse-clean for machine consumers.
    print(
        json.dumps(
            {"target_branch": policy.target_branch, "deploy_gate": policy.deploy_gate}
        )
    )


@app.command("parse-board")
def parse_board_cmd(
    path: str = typer.Argument(
        "-", help="Path to the board comment body, or '-' to read stdin."
    ),
) -> None:
    """Parse an orchestrate-epic supervision-board comment into JSON rows.

    Emits {rows: [{feature, issue, tier, batch, state, pr}, ...]}. A missing
    marker, missing columns, or a malformed row exits 1 with a clear error on
    stderr — never a silent misread.
    """
    text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    try:
        rows = parse_board(text)
    except BoardParseError as exc:
        print(f"board parse error: {exc}", file=sys.stderr)
        raise typer.Exit(code=1) from exc
    # stdout, not rich console: keep it parse-clean for machine consumers.
    print(json.dumps({"rows": [asdict(row) for row in rows]}))


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
