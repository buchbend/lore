"""`lore lint` — health-check the vault, regenerate catalogs.

`lore lint adr` is the one sub-verb: it checks a repo's decision records
(ADR 0015), not the vault.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer
from lore_core.lint import run_lint
from lore_workflow.adr_lint import lint_adrs

from lore_cli._argv_compat import argv_main

app = typer.Typer(
    add_completion=False,
    help="Lore linter — scan all wikis, check health, regenerate catalogs.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)


@app.callback(invoke_without_command=True)
def lint(
    ctx: typer.Context,
    wiki: str = typer.Option(None, "--wiki", "-w", help="Scope to a single wiki."),
    check_only: bool = typer.Option(False, "--check-only", help="Lint only, skip catalog writes."),
    json_out: bool = typer.Option(False, "--json", help="Output report as JSON."),
) -> None:
    """Lint the vault and (re)generate catalogs."""
    if ctx.invoked_subcommand is not None:
        return
    report = run_lint(
        wiki_filter=wiki,
        check_only=check_only,
        json_output=json_out,
    )
    if report.get("by_severity", {}).get("errors", 0) > 0:
        raise typer.Exit(code=1)


@app.command("adr")
def lint_adr(
    root: Annotated[
        str, typer.Argument(help="Repo root that holds docs/adr/ (default: cwd).")
    ] = ".",
    json_out: Annotated[bool, typer.Option("--json", help="Output report as JSON.")] = False,
) -> None:
    """Check each ADR from 0014 on: a `## Holds` section and a real test per invariant.

    Exits 1 and names the file and line of each problem. ADRs 0001 to 0013 are skipped.
    """
    report = lint_adrs(Path(root))
    if json_out:
        # stdout, not rich console: keep it parse-clean for machine consumers.
        print(
            json.dumps(
                {
                    "ok": report.ok,
                    "checked": report.checked,
                    "skipped": report.skipped,
                    "problems": [asdict(p) for p in report.problems],
                }
            )
        )
    else:
        for problem in report.problems:
            typer.echo(str(problem))
        typer.echo(
            f"lint adr: {len(report.checked)} checked, {len(report.skipped)} skipped, "
            f"{len(report.problems)} problem(s)"
        )
    if not report.ok:
        raise typer.Exit(code=1)


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
