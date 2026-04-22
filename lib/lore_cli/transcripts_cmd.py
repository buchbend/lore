"""`lore transcripts` — mirror + inspect Claude Code transcripts locally.

Privacy boundary: originals under ``~/.claude/projects/...`` are the
user's property. We mirror them into ``<wiki>/.transcripts/<uuid>.jsonl``
(gitignored) so ``lore transcripts show`` can restore full context
without exposing raw data through the wiki's backend.

Subcommands:

* ``lore transcripts sync [--wiki NAME]``
* ``lore transcripts show <uuid> [--last N]``

``prune`` is intentionally omitted — it can be added when users ask
for it. Until then, manual ``rm`` under each wiki's ``.transcripts/``
is documented in the wiki template.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import typer
from rich.console import Console

from lore_cli._compat import argv_main
from lore_core.transcript_sync import sync_transcripts

console = Console()
err_console = Console(stderr=True)

app = typer.Typer(
    add_completion=False,
    help="Mirror + inspect Claude Code transcripts locally.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _lore_root_or_die() -> Path:
    env = os.environ.get("LORE_ROOT")
    if not env:
        err_console.print("[red]LORE_ROOT is not set[/red]")
        raise typer.Exit(code=2)
    root = Path(env)
    if not root.exists():
        err_console.print(f"[red]LORE_ROOT does not exist: {root}[/red]")
        raise typer.Exit(code=2)
    return root


@app.command("sync", help="Mirror every attached transcript into its wiki's .transcripts/.")
def cmd_sync(
    wiki: str | None = typer.Option(
        None, "--wiki", help="Restrict to this wiki name only."
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit a machine-readable summary."
    ),
) -> None:
    lore_root = _lore_root_or_die()
    result = sync_transcripts(lore_root, wiki=wiki)

    if json_out:
        typer.echo(
            json.dumps(
                {
                    "copied": result.copied,
                    "skipped": result.skipped,
                    "errors": result.errors,
                },
                indent=2,
            )
        )
    else:
        console.print(
            f"[green]{result.copied}[/green] copied · "
            f"{result.skipped} up-to-date · "
            f"[red]{len(result.errors)}[/red] error(s)"
        )
        for e in result.errors:
            err_console.print(f"  • {e}")

    if result.errors:
        raise typer.Exit(code=1)


def _resolve_transcript_path(lore_root: Path, uuid: str) -> Path | None:
    """Find ``<uuid>.jsonl`` under any wiki's ``.transcripts/`` directory.

    If the mirror doesn't exist, fall back to the source location under
    ``~/.claude/projects/``. Returns None when neither path holds a file.
    """
    for mirror in (lore_root / "wiki").glob(f"*/.transcripts/{uuid}.jsonl"):
        if mirror.is_file():
            return mirror
    # Fallback: scan original locations. Every Claude Code project-dir
    # holds a session per cwd; searching every one of them is a
    # glob over a shallow tree with a small branching factor.
    projects = Path.home() / ".claude" / "projects"
    if projects.exists():
        for candidate in projects.glob(f"*/{uuid}.jsonl"):
            if candidate.is_file():
                return candidate
    return None


@app.command("show", help="Print a transcript's text turns to stdout (mirror first, source fallback).")
def cmd_show(
    uuid: str = typer.Argument(..., help="Session UUID (no .jsonl suffix)."),
    last: int = typer.Option(
        0, "--last", help="Print only the last N text turns. 0 = all."
    ),
) -> None:
    lore_root = _lore_root_or_die()
    path = _resolve_transcript_path(lore_root, uuid)
    if path is None:
        err_console.print(
            f"[red]No transcript found for uuid {uuid!r}. "
            f"Run `lore transcripts sync` or check ~/.claude/projects/.[/red]"
        )
        raise typer.Exit(code=1)

    # Collect user/assistant text turns; everything else (tool_use /
    # tool_result / attachment / queue-operation) is dropped — `show`
    # is for recalling the narrative, not the machinery.
    turns: list[tuple[str, str]] = []  # (role, text)
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fp:
            for raw in fp:
                line = raw.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(ev, dict):
                    continue
                if ev.get("type") not in ("user", "assistant"):
                    continue
                msg = ev.get("message")
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role") or ev.get("type")
                content = msg.get("content")
                if isinstance(content, str):
                    text = content.strip()
                    if text:
                        turns.append((role, text))
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = (block.get("text") or "").strip()
                            if text:
                                turns.append((role, text))
    except OSError as exc:
        err_console.print(f"[red]cannot read {path}: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    if last > 0:
        turns = turns[-last:]

    console.print(f"[dim]{path}[/dim]\n")
    for role, text in turns:
        console.print(f"[bold]{role}:[/bold] {text}\n")


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
