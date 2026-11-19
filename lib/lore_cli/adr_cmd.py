"""`lore adr check` — check a repo's decision records (ADR 0015)."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer
from lore_workflow.adr_lint import lint_adrs

from lore_cli._argv_compat import argv_main

app = typer.Typer(
    add_completion=False,
    help="Check a repo's decision records.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


@app.callback()
def _group() -> None:
    """Check a repo's decision records."""
    # A callback keeps `check` a named command: Typer runs a lone command as the app itself.


@app.command("check")
def check(
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
            f"adr check: {len(report.checked)} checked, {len(report.skipped)} skipped, "
            f"{len(report.problems)} problem(s)"
        )
    if not report.ok:
        raise typer.Exit(code=1)


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
