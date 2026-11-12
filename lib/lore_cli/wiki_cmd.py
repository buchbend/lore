"""`lore wiki ...` — manage individual wikis under $LORE_ROOT/wiki/.

The canonical home for wiki-lifecycle verbs going forward. It hosts
``new`` (scaffold), ``check`` and ``commit`` (one wiki entry, issue #601).
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict
from enum import Enum
from pathlib import Path

import typer
from lore_core.config import get_wiki_root
from rich.console import Console

from lore_cli._argv_compat import argv_main

console = Console()

app = typer.Typer(
    add_completion=False,
    help=__doc__,
    no_args_is_help=True,
    rich_markup_mode="rich",
)


class WikiMode(str, Enum):
    personal = "personal"
    team = "team"


SUBDIRS = ("projects", "concepts", "decisions", "inbox")

# Seeded on wiki creation. Fully commented so `load_scopes_yml` parses it
# to an empty tree — a starting point, never a phantom scope. The shape
# mirrors what `lore_core.scopes.walk_scope_leaves` reads: a top-level
# `scopes:` map whose leaves carry a `repo:` and may nest via `children:`.
_SCOPES_YML_TEMPLATE = """\
# _scopes.yml — declare which repos live under which scope path.
#
# Scopes are colon-separated and hierarchical
# (e.g. ccat:data-center:data-transfer). Each leaf with a `repo:` maps a
# git repo slug (owner/name) onto a scope path; nest deeper with
# `children:`. Uncomment and adapt the example below to get started.
#
# scopes:
#   my-project:
#     repo: your-org/my-project
#     children:
#       backend:
#         repo: your-org/my-project-backend
"""


def _plugin_templates_dir() -> Path:
    from lore_core.templates import templates_dir

    return templates_dir()


def scaffold_wiki(
    name: str,
    *,
    mode: str = "personal",
    remote: str | None = None,
    force: bool = False,
) -> Path:
    wiki_root = get_wiki_root()
    wiki_root.mkdir(parents=True, exist_ok=True)
    target = wiki_root / name

    is_new_wiki = not target.exists()

    if is_new_wiki or force:
        target.mkdir(exist_ok=True)
        for sub in SUBDIRS:
            (target / sub).mkdir(exist_ok=True)

        templates_src = _plugin_templates_dir()
        claude_md = target / "CLAUDE.md"
        claude_md.write_text((templates_src / "wiki-CLAUDE.md").read_text())

        # Seed a commented `_scopes.yml` so the scope registry has an
        # obvious home the moment the wiki exists (never overwrite one a
        # cloned/linked wiki already carries).
        scopes_yml = target / "_scopes.yml"
        if not scopes_yml.exists():
            scopes_yml.write_text(_SCOPES_YML_TEMPLATE)

    if mode == "team":
        subprocess.run(["git", "init"], cwd=str(target), check=False)
        if remote:
            subprocess.run(
                ["git", "remote", "add", "origin", remote],
                cwd=str(target),
                check=False,
            )
        subprocess.run(["git", "add", "-A"], cwd=str(target), check=False)
        subprocess.run(
            ["git", "commit", "-m", "lore: initial wiki scaffold"],
            cwd=str(target),
            check=False,
        )

    console.print(f"[green]Created {target}[/green]")
    return target


@app.command("new")
def cmd_new(
    name: str = typer.Argument(..., help="Wiki name (kebab-case)."),
    mode: WikiMode = typer.Option(
        WikiMode.personal,
        "--mode",
        help="`team` mode adds git init + optional remote.",
    ),
    remote: str = typer.Option(None, "--remote", help="Git remote URL (team mode)."),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing wiki directory."),
) -> None:
    """Scaffold a new wiki under $LORE_ROOT/wiki/."""
    scaffold_wiki(name, mode=mode.value, remote=remote, force=force)


def _wiki_of(path: Path) -> tuple[Path, str]:
    """The wiki that holds the note file `path`, and the note's path relative to it."""
    from lore_core.wiki_check import discover_wikis

    target = path.resolve()
    wikis = [w for w in discover_wikis() if target.is_relative_to(w.resolve())]
    if not target.is_file() or not wikis:
        typer.echo(f"error: {path} is not a note file inside a wiki", err=True)
        raise typer.Exit(code=2)
    return wikis[0], target.relative_to(wikis[0].resolve()).as_posix()


@app.command("commit")
def cmd_commit(
    entry: Path = typer.Argument(..., help="The wiki entry file to commit."),
) -> None:
    """Check one wiki entry, commit it and push it at once.

    Refuses on an error for the entry; errors in other notes do not block.
    Runs `git init` in a wiki that is no repo. Commits the person's other
    uncommitted edits first, in their own commit. With a remote, merges the
    remote changes first and pushes after the commit. Exits 1 on a check
    error or a merge conflict; a conflict keeps the local commit.
    """
    from lore_core.git_sync import commit_entry
    from lore_core.wiki_check import check_wiki

    wiki_path, rel = _wiki_of(entry)
    findings, _ = check_wiki(wiki_path, rel)
    errors = [f for f in findings if f.severity == "error"]
    for f in errors:
        typer.echo(f"{rel}" + (f":{f.line}" if f.line else "") + f": error: {f.check}: {f.message}")
    if errors:
        typer.echo("not committed: fix the errors of the entry first", err=True)
        raise typer.Exit(code=1)

    try:
        result = commit_entry(wiki_path.resolve(), rel)
    except subprocess.CalledProcessError as exc:
        typer.echo(
            f"error: git {' '.join(exc.cmd[1:3])} failed: {(exc.stderr or '').strip()}", err=True
        )
        raise typer.Exit(code=1) from exc
    typer.echo(f"entry: {wiki_path / rel}")
    typer.echo(f"commit: {result.commit or 'none (no change)'}")
    if result.pushed:
        typer.echo("pushed")
    if result.conflict:
        typer.echo("merge conflict, local commit kept, not pushed: " + ", ".join(result.conflict))
        raise typer.Exit(code=1)
    if result.message:
        typer.echo(f"not pushed: {result.message}")


@app.command("check")
def cmd_check(
    path: Path = typer.Argument(None, help="One note file. Default: every note."),
    wiki: str = typer.Option(None, "--wiki", "-w", help="Check one wiki. Default: every wiki."),
    json_out: bool = typer.Option(False, "--json", help="Output the findings as JSON."),
) -> None:
    """Check wiki notes: wikilinks, description, filename and the writing rules.

    Exits 1 when a check reports an error. Checks no wiki structure and writes no file.
    """
    from lore_core.wiki_check import check_wiki, discover_wikis

    only = None
    if path is not None:
        wiki_path, only = _wiki_of(path)
        wikis = [wiki_path]
    else:
        wikis = discover_wikis(wiki)
        if not wikis:
            typer.echo(f"error: no wiki found in {get_wiki_root()}", err=True)
            raise typer.Exit(code=2)

    findings = []
    notes: list[str] = []
    for wiki_path in wikis:
        found, note = check_wiki(wiki_path, only)
        findings += found
        if note and note not in notes:
            notes.append(note)

    counts = dict.fromkeys(("error", "warning", "suggestion"), 0)
    for f in findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1
    if json_out:
        # Plain stdout: Rich would eat the `[[...]]` of a wikilink.
        print(json.dumps({"findings": [asdict(f) for f in findings], "notes": notes}))
    else:
        for f in findings:
            where = f"{f.wiki}/{f.file}" + (f":{f.line}" if f.line else "")
            typer.echo(f"{where}: {f.severity}: {f.check}: {f.message}")
        for note in notes:
            typer.echo(note)
        typer.echo("wiki check: " + ", ".join(f"{n} {level}(s)" for level, n in counts.items()))
    if counts["error"]:
        raise typer.Exit(code=1)


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
