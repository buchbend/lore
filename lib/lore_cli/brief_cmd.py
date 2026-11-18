"""`lore brief --json "<intent>"` — the orient homework in one call.

Prints one JSON object: the ranked context pack, the first section of each
ranked doc, a code-map excerpt of about 1000 tokens, and the glossary entries
of the domain words in the intent. Dig deeper with a `lore.api` script.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from lore_core.brief import brief

app = typer.Typer(
    add_completion=False,
    help=__doc__,
    rich_markup_mode="rich",
    # A group stops parsing options at its first argument by default.
    context_settings={"allow_interspersed_args": True},
)


@app.callback(invoke_without_command=True)
def brief_cmd(
    intent: Annotated[str, typer.Argument(help="What the work is about, in a few words.")],
    json_out: Annotated[
        bool, typer.Option("--json", help="Print JSON (the only output format).")
    ] = True,
    cwd: Annotated[
        Path | None, typer.Option("--cwd", help="A folder inside the repo (default: cwd).")
    ] = None,
) -> None:
    """Print the brief for INTENT as JSON."""
    print(json.dumps(brief(intent, cwd=cwd), indent=2, ensure_ascii=False))
