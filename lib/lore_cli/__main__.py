"""`lore` command — top-level typer dispatcher.

Each subcommand is implemented as its own typer app in a sibling
module; this file mounts them under a single root so `lore --help`
renders the full subcommand tree with Rich-styled boxes and each
`lore <verb> --help` works uniformly.
"""

from __future__ import annotations

import sys

import click
import typer

# Subcommand apps — every one of these is a typer.Typer instance with
# its own commands / callback. Registering them via add_typer gives a
# unified `lore --help` listing.
from lore_cli import (
    attach_cmd,
    attachments_cmd,
    backfill_cmd,
    briefing_cmd,
    completions_cmd,
    config_cmd,
    detach_cmd,
    doctor_cmd,
    hooks,
    inbox_cmd,
    ingest_cmd,
    init_cmd,
    install_cmd,
    log_cmd,
    news_cmd,
    new_wiki_cmd,
    proc_cmd,
    registry_cmd,
    resume_cmd,
    runs_cmd,
    scopes_cmd,
    session_cmd,
    status_cmd,
    surface_cmd,
    transcripts_cmd,
    wiki_cmd,
)
from lore_core import lint as lint_cmd
from lore_core import migrate as migrate_cmd
from lore_curator import defrag_curator as curator_cmd
from lore_mcp import server as mcp_cmd
from lore_search import cli as search_cmd

app = typer.Typer(
    add_completion=False,
    help="lore — knowledge-graph tooling for AI-coding teams.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

# Mount subcommands grouped by audience. `rich_help_panel` controls the
# section header in `lore --help`.
_GS = "Getting Started"
_KN = "Knowledge"
_ADV = "Advanced"

app.add_typer(install_cmd.app, name="install", rich_help_panel=_GS)
app.add_typer(init_cmd.app, name="init", rich_help_panel=_GS)
app.add_typer(attach_cmd.app, name="attach", rich_help_panel=_GS)
app.add_typer(status_cmd.app, name="status", rich_help_panel=_GS)
app.add_typer(doctor_cmd.app, name="doctor", rich_help_panel=_GS)
app.add_typer(config_cmd.app, name="config", rich_help_panel=_GS)

app.add_typer(search_cmd.app, name="search", rich_help_panel=_KN)
app.add_typer(session_cmd.app, name="session", rich_help_panel=_KN)
app.add_typer(surface_cmd.app, name="surface", rich_help_panel=_KN)
app.add_typer(wiki_cmd.app, name="wiki", rich_help_panel=_KN)
app.add_typer(news_cmd.app, name="news", rich_help_panel=_KN)
app.add_typer(resume_cmd.app, name="resume", rich_help_panel=_KN)
app.add_typer(lint_cmd.app, name="lint", rich_help_panel=_KN)
app.add_typer(curator_cmd.app, name="curator", rich_help_panel=_KN)
# Legacy alias: `lore new-wiki <name>` forwards to the same scaffolder
# as `lore wiki new <name>`. Kept for backward compat with users who
# typed the old form during 0.x; the canonical path is `lore wiki new`.
app.add_typer(new_wiki_cmd.app, name="new-wiki", rich_help_panel=_ADV)

app.add_typer(backfill_cmd.app, name="backfill", rich_help_panel=_ADV)
app.add_typer(attachments_cmd.app, name="attachments", rich_help_panel=_ADV)
app.add_typer(briefing_cmd.app, name="briefing", rich_help_panel=_ADV)
app.add_typer(completions_cmd.app, name="completions", rich_help_panel=_ADV)
app.add_typer(detach_cmd.app, name="detach", rich_help_panel=_ADV)
app.add_typer(hooks.hook_app, name="hook", rich_help_panel=_ADV)
app.add_typer(inbox_cmd.app, name="inbox", rich_help_panel=_ADV)
app.add_typer(ingest_cmd.app, name="ingest", rich_help_panel=_ADV)
app.add_typer(log_cmd.app, name="log", rich_help_panel=_ADV)
app.add_typer(mcp_cmd.app, name="mcp", rich_help_panel=_ADV)
app.add_typer(migrate_cmd.app, name="migrate", rich_help_panel=_ADV)
app.add_typer(proc_cmd.app, name="proc", rich_help_panel=_ADV)
app.add_typer(registry_cmd.app, name="registry", rich_help_panel=_ADV)
app.add_typer(runs_cmd.app, name="runs", rich_help_panel=_ADV)
app.add_typer(scopes_cmd.app, name="scopes", rich_help_panel=_ADV)
app.add_typer(transcripts_cmd.app, name="transcripts", rich_help_panel=_ADV)


@app.command(
    "uninstall",
    help="Symmetric semantic remove (alias for `install uninstall`).",
)
def cmd_uninstall_alias(
    host: str = install_cmd._HOST,
    yes: bool = install_cmd._YES,
    quiet: bool = install_cmd._QUIET,
    json_out: bool = install_cmd._JSON,
    force: bool = install_cmd._FORCE,
    lore_repo: str = install_cmd._LORE_REPO,
) -> None:
    """Top-level `lore uninstall` — same flags as `lore install uninstall`."""
    args = install_cmd._make_args(
        "uninstall",
        host=host,
        yes=yes,
        quiet=quiet,
        json_out=json_out,
        force=force,
        lore_repo=lore_repo,
    )
    install_cmd._exit_with(install_cmd._cmd_install(args, mode="uninstall"))


def main(argv: list[str] | None = None) -> int:
    """Entry point — `lore` and `python -m lore_cli`."""
    if argv is None:
        argv = sys.argv[1:]
    try:
        result = app(args=argv, standalone_mode=False)
        if isinstance(result, int):
            return result
        return 0
    except click.exceptions.ClickException as e:
        e.show()
        return e.exit_code
    except click.exceptions.Abort:
        print("Aborted.", file=sys.stderr)
        return 130
    except typer.Exit as e:
        return int(e.exit_code or 0)
    except SystemExit as e:
        code = e.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        if isinstance(code, str):
            print(code, file=sys.stderr)
            return 1
        return 1


if __name__ == "__main__":
    sys.exit(main())
