"""`lore curator` — the deterministic hygiene pass.

Bare `lore curator [--wiki] [--apply]` runs the hygiene passes
(supersession, `implements:` back-links, git-backfill of dates, team-mode
hint) and writes `_review.md` per wiki.

Frontmatter-only judgement calls, no model in the loop. The subcommands that
composed session notes from pending transcripts — `run`, `flush`, `reap`,
`sweep` — retired with the compose pipeline; a session's record is now its
transcript-ledger entry, and the deliberate crossing to a wiki is `lore flag`.
"""

from __future__ import annotations

import sys

import typer
from rich.console import Console

from lore_cli._argv_compat import argv_main

console = Console()

app = typer.Typer(
    add_completion=False,
    help="Curator — flag superseded notes, propagate `implements:`, backfill dates.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)


@app.callback(invoke_without_command=True)
def curator(
    ctx: typer.Context,
    wiki: str = typer.Option(None, "--wiki", help="Scope to one wiki."),
    apply: bool = typer.Option(
        False, "--apply", help="Actually write changes. Without this, runs dry."
    ),
) -> None:
    """Run the hygiene passes — supersession, implements, git backfill."""
    if ctx.invoked_subcommand is not None:
        return

    from lore_curator.hygiene import run_hygiene

    run_hygiene(wiki_filter=wiki, dry_run=not apply)


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
