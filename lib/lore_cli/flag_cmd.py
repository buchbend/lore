"""``lore flag`` — file one team-relevant fact, and review the ones filed.

A flag is a deliberate crossing from a working session to the wiki: one
stamped fact, appended to the owning topic note the moment it appears.
It is the only crossing: lore writes nothing else into a wiki from a
session.

  lore flag write "lead sentence" --body "why it matters" --ref pr:357
  lore flag review                     walk the unreviewed flags
  lore flag list                       what is pending, without acting

Agents reach the same write path through the ``lore_flag`` MCP tool and
land marked unreviewed. A flag written here is human-authored: it lands
without the marker and keeps its own words, because the code-stamped
phrasing of ``docs/adr/0004`` constrains what a model may claim, not what
a person writes. Pass ``--agent`` when a session files on the model's
behalf from a shell.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer
from lore_core import flag
from lore_core.config import get_wiki_root
from lore_core.git import current_repo, git_repo_root

from lore_cli._argv_compat import argv_main

app = typer.Typer(
    add_completion=False,
    help=__doc__,
    no_args_is_help=True,
    rich_markup_mode="rich",
)

_VERDICT_PROMPT = "accept / retarget / decline / skip? [a/r/d/s]"

# Hoisted out of the signature: a repeatable option's default is a list, which
# a call in a default argument may not be.
_REF_OPTION = typer.Option(
    None, "--ref", help="Evidence as TYPE:VALUE (pr/issue/commit/file/tag). Repeatable."
)


def _emit_json(envelope: dict) -> None:
    print(json.dumps(envelope, indent=2, default=str))


def _parse_refs(raw: list[str] | None) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    for item in raw or []:
        if ":" not in item:
            raise typer.BadParameter(f"--ref wants TYPE:VALUE, got {item!r}")
        ref_type, value = item.split(":", 1)
        refs.append((ref_type.strip(), value.strip()))
    return refs


def _resolve_wiki_path(wiki: str | None) -> Path:
    """Wiki directory for the review side — named, or resolved from cwd."""
    if wiki:
        return get_wiki_root() / wiki
    from lore_core.scope_resolver import resolve_scope

    scope = resolve_scope(Path.cwd())
    if scope is None:
        typer.echo(
            "lore: no wiki resolved — pass --wiki, or run inside an attached repo",
            err=True,
        )
        raise typer.Exit(code=2)
    return get_wiki_root() / scope.wiki


@app.command("write")
def cmd_write(
    lead: str = typer.Argument(..., help="The fact, one sentence. Wrap in quotes."),
    body: str = typer.Option("", "--body", "-b", help="Why it is worth keeping."),
    wiki: str | None = typer.Option(None, "--wiki", help="Wiki name (default: from cwd)."),
    target: str | None = typer.Option(
        None, "--target", "-t", help="Owning note: wiki-relative path or slug."
    ),
    ref: list[str] | None = _REF_OPTION,
    transcript: str | None = typer.Option(
        None, "--transcript", help="Transcript id (default: $CLAUDE_SESSION_ID)."
    ),
    author: str | None = typer.Option(None, "--author", help="Override the author tag."),
    agent: bool = typer.Option(
        False, "--agent", help="File on a model's behalf: stamped, lands unreviewed."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON envelope."),
) -> None:
    """Append one flag to its owning topic note."""
    cwd = Path.cwd()
    try:
        result = flag.write(
            lead,
            body,
            wiki=wiki,
            target=target,
            refs=_parse_refs(ref),
            transcript=transcript,
            author=author,
            human=not agent,
            cwd=cwd,
            repo_root=git_repo_root(cwd),
            repo=current_repo(cwd) or "",
        )
    except flag.OriginMissing as e:
        typer.echo(f"lore: {e}", err=True)
        raise typer.Exit(code=1) from None
    except ValueError as e:
        typer.echo(f"lore: {e}", err=True)
        raise typer.Exit(code=1) from None

    if json_out:
        _emit_json({"schema": "lore.flag.write/1", "data": result.__dict__})
        return
    if result.status == "withheld":
        typer.echo(
            f"withheld ({result.category}) — text held in quarantine "
            f"{result.quarantine_id}; `lore quarantine show {result.quarantine_id}`"
        )
        raise typer.Exit(code=1)
    verb = "created" if result.created_note else "appended to"
    typer.echo(f"flag {result.flag_id} · {verb} {result.note_path}")


@app.command("list")
def cmd_list(
    wiki: str | None = typer.Option(None, "--wiki", help="Wiki name (default: from cwd)."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON envelope."),
) -> None:
    """Show unreviewed flags without acting on them."""
    wiki_path = _resolve_wiki_path(wiki)
    items = flag.pending(wiki_path)
    if json_out:
        _emit_json(
            {
                "schema": "lore.flag.list/1",
                "data": [{"id": f.id, "note": f.note_path, "lead": f.lead} for f in items],
            }
        )
        return
    if not items:
        typer.echo("(no pending flags)")
        return
    for item in items:
        typer.echo(f"{item.id}  {Path(item.note_path).stem}  {item.lead}")
    typer.echo("")
    typer.echo(f"{len(items)} pending — `lore flag review` to walk them")


@app.command("review")
def cmd_review(
    wiki: str | None = typer.Option(None, "--wiki", help="Wiki name (default: from cwd)."),
    tty: bool = typer.Option(False, "--tty", help="Prompt in the terminal instead of a browser."),
) -> None:
    """Walk the unreviewed flags: accept, retarget, decline, or skip.

    The browser page is the default surface: a flag's ref verdict decides
    how much its lead may claim (``docs/adr/0004``), and in one terminal
    colour that verdict reads as prose. The terminal prompts remain, and
    take over on a host where no browser resolves.

    In the terminal, the list is snapshotted before the walk starts, so a
    retarget that moves a flag into a note already visited cannot show it
    twice.
    """
    from lore_cli import flag_review_html

    wiki_path = _resolve_wiki_path(wiki)
    items = flag.pending(wiki_path)
    if not items:
        typer.echo("(no pending flags)")
        return

    if not tty and flag_review_html.browser_available():
        typer.echo(f"{len(items)} pending — opening the review page, Ctrl-C to stop")
        flag_review_html.serve(wiki_path)
        return

    for i, item in enumerate(items, start=1):
        typer.echo("")
        typer.echo(f"--- {i}/{len(items)} · {Path(item.note_path).stem} ---")
        typer.echo(item.block)
        typer.echo("")
        choice = typer.prompt(_VERDICT_PROMPT, default="s").strip().lower()[:1]
        if choice == "a":
            flag.accept(wiki_path, item.id)
            typer.echo("accepted")
        elif choice == "d":
            flag.decline(wiki_path, item.id)
            typer.echo("declined")
        elif choice == "r":
            target = typer.prompt("target note (wiki-relative path or slug)").strip()
            try:
                moved = flag.retarget(wiki_path, item.id, target)
            except ValueError as e:
                # A bad target loses this one verdict, never the walk.
                typer.echo(f"lore: {e}", err=True)
                continue
            typer.echo(f"moved to {moved}" if moved else "not moved")
        else:
            typer.echo("skipped")


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
