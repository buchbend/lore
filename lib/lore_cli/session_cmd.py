"""`lore session commit` — git-add + commit one note inside a wiki repo.

Auto-capture (curator A → ``lore_curator/session_filer.py`` →
``lore_core/session_writer.py``) is the canonical write path; it does
its own commit when ``auto_commit: true`` is set on the wiki. This CLI
verb exists for the explicit-write skills (inbox, briefing) that author
a note in-thread and need to commit it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from lore_cli._argv_compat import argv_main
from lore_core.session import commit_note

app = typer.Typer(
    add_completion=False,
    help=__doc__,
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _emit_json(envelope: dict) -> None:
    print(json.dumps(envelope, indent=2))


@app.command("new", hidden=True)
def cmd_new() -> None:
    """Removed — auto-capture (curator A) is the canonical write path.

    Kept as a hidden stub so Typer keeps the ``commit`` subcommand
    addressable as ``lore session commit`` (a single-command Typer app
    auto-flattens, which would break callers like the inbox + briefing
    skills).
    """
    print(
        "lore session new is gone — auto-capture (curator A) writes session "
        "notes from the transcript automatically.",
        file=sys.stderr,
    )
    raise typer.Exit(code=2)


@app.command("commit")
def cmd_commit(
    path: str = typer.Argument(..., help="Path to the note inside a wiki."),
    message: str = typer.Option(
        None, "--message", "-m", help="Override commit message (default: `lore: session <slug>`)."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON envelope."),
) -> None:
    """git add + commit one note in its wiki repo."""
    note_path = Path(path).resolve()
    if not note_path.exists():
        print(f"lore: not found: {note_path}", file=sys.stderr)
        raise typer.Exit(code=1)

    from lore_core.config import get_wiki_root

    wiki_root = get_wiki_root().resolve()
    wiki_path: Path | None = None
    for parent in note_path.parents:
        if parent.parent == wiki_root:
            wiki_path = parent
            break
    if wiki_path is None:
        print(
            f"lore: {note_path} is not inside any wiki under {wiki_root}",
            file=sys.stderr,
        )
        raise typer.Exit(code=1)

    ok, sha_or_err = commit_note(
        wiki_path=wiki_path,
        note_path=note_path,
        message=message,
    )
    if json_out:
        _emit_json(
            {
                "schema": "lore.session.commit/1",
                "data": {
                    "ok": ok,
                    "sha": sha_or_err if ok else "",
                    "error": sha_or_err if not ok else None,
                    "wiki": wiki_path.name,
                    "path": str(note_path.relative_to(wiki_path)),
                },
            }
        )
    else:
        if ok:
            print(sha_or_err or "(nothing to commit — already committed)")
        else:
            print(f"lore: commit failed: {sha_or_err}", file=sys.stderr)
    if not ok:
        raise typer.Exit(code=1)


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
