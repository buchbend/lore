"""`lore session` — scaffold/write/commit session notes from the shell.

Two subcommands:

  lore session new    — write a new session note from a scaffold + body
  lore session commit — git-add + commit a session note in its wiki repo

These are the side-effecting (write) half of the session pipeline. The
read-only counterpart is the MCP tool `lore_session_scaffold`. The
`lore-session-writer` subagent calls scaffold via MCP first (silent,
fast), composes the prose body, then shells out here to make the writes
visible/auditable to the user.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from lore_runtime.argv import argv_main
from lore_core.session import commit_note, scaffold, write_note

app = typer.Typer(
    add_completion=False,
    help=__doc__,
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _split_csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


def _emit_json(envelope: dict) -> None:
    print(json.dumps(envelope, indent=2))


# ---------------------------------------------------------------------------
# `lore session new`
# ---------------------------------------------------------------------------


@app.command("new")
def cmd_new(
    cwd: str = typer.Option(..., "--cwd", help="Working directory the session ran in."),
    slug: str = typer.Option(..., "--slug", help="Short kebab-case topic."),
    description: str = typer.Option(..., "--description", help="One-sentence summary."),
    title: str = typer.Option(None, "--title", help="Note H1 title (default: slug)."),
    target_wiki: str = typer.Option(
        None,
        "--target-wiki",
        help="Wiki name (default: from `## Lore` block or only-wiki).",
    ),
    repos: str = typer.Option(None, "--repos", help="Comma-separated extra repos."),
    tags: str = typer.Option(None, "--tags", help="Comma-separated tags."),
    implements: str = typer.Option(
        None, "--implements", help="Comma-separated proposal slugs that landed."
    ),
    loose_end: list[str] = typer.Option(
        None,
        "--loose-end",
        help="Repeatable — long-form loose-end strings.",
    ),
    project: str = typer.Option(None, "--project"),
    body: str = typer.Option(
        None,
        "--body",
        help="Path to body markdown, or `-` for stdin (default: scaffold's stub template).",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Compute the path + frontmatter; do not write."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON envelope."),
) -> None:
    """Scaffold + write a new session note. Body comes from --body file or stdin."""
    body_text: str
    if body == "-":
        body_text = sys.stdin.read()
    elif body:
        body_text = Path(body).read_text()
    else:
        body_text = ""  # subagent may have only the scaffold's body_template

    result = scaffold(
        cwd=cwd,
        slug=slug,
        description=description,
        title=title,
        target_wiki=target_wiki,
        extra_repos=_split_csv(repos),
        tags=_split_csv(tags),
        implements=_split_csv(implements),
        loose_ends=loose_end or None,
        project=project,
    )

    if "error" in result:
        if json_out:
            _emit_json({"schema": "lore.session.new/1", "data": result})
        else:
            print(f"lore: {result['error']}", file=sys.stderr)
        raise typer.Exit(code=1)

    note_path = Path(result["note_path"])
    body_to_write = body_text or result["body_template"]
    if dry_run:
        if json_out:
            _emit_json(
                {"schema": "lore.session.new/1", "data": {**result, "dry_run": True}}
            )
        else:
            print(f"would write: {note_path}", file=sys.stderr)
        return

    filed = write_note(scaffolded=result, body=body_to_write)

    if json_out:
        _emit_json(
            {
                "schema": "lore.session.new/1",
                "data": {
                    **{k: v for k, v in result.items() if k != "body_template"},
                    "note_path": str(filed.path),
                    "wikilink": filed.wikilink,
                    "was_merge": filed.was_merge,
                    "written": True,
                },
            }
        )
    else:
        print(str(filed.path))


# ---------------------------------------------------------------------------
# `lore session commit`
# ---------------------------------------------------------------------------


@app.command("commit")
def cmd_commit(
    path: str = typer.Argument(..., help="Path to the session note inside a wiki."),
    message: str = typer.Option(
        None, "--message", "-m", help="Override commit message (default: `lore: session <slug>`)."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON envelope."),
) -> None:
    """git add + commit one session note in its wiki repo."""
    note_path = Path(path).resolve()
    if not note_path.exists():
        print(f"lore: not found: {note_path}", file=sys.stderr)
        raise typer.Exit(code=1)

    # Wiki root is the nearest ancestor matching $LORE_ROOT/wiki/<name>/
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
