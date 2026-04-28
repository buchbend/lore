"""``lore plan`` — list / delete / import / step / advance plan notes.

Five subcommands:

  lore plan list      — enumerate active plans (optionally filtered by repo).
  lore plan delete    — remove a plan note (refuses on incoming wikilinks
                        without ``--force``; confirms on ``status: active``).
  lore plan import    — re-ingest an orphan-dumped JSON envelope OR a raw
                        markdown file (e.g. historical ``~/.claude/plans/``).
                        Modes are explicit: ``--from-orphan`` / ``--from-markdown``;
                        bare invocation dispatches by extension.
  lore plan step      — set ``step_status[<step_id>]`` atomically.
  lore plan advance   — sugar: mark the current in-progress step done; else
                        the next pending step.

Hook-side capture is ``lore hook plan-capture`` (lives in ``hooks.py`` for
dispatcher consistency).
"""
from __future__ import annotations

import json
import sys
from datetime import date as _date
from pathlib import Path

import typer

from lore_cli._argv_compat import argv_main

app = typer.Typer(
    add_completion=False,
    help=__doc__,
    no_args_is_help=True,
    rich_markup_mode="rich",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _emit_json(envelope: dict) -> None:
    print(json.dumps(envelope, indent=2))


def _resolve_wiki(target_wiki: str | None) -> Path:
    """Resolve the wiki root path. Raises typer.Exit on ambiguity."""
    from lore_core.config import get_wiki_root

    wiki_root = get_wiki_root()
    if not wiki_root.exists():
        typer.echo(f"lore: vault root not found: {wiki_root}", err=True)
        raise typer.Exit(code=1)
    if target_wiki:
        candidate = wiki_root / target_wiki
        if not candidate.exists():
            typer.echo(f"lore: wiki not found: {target_wiki}", err=True)
            raise typer.Exit(code=1)
        return candidate
    wikis = [p for p in sorted(wiki_root.iterdir()) if p.is_dir()]
    if len(wikis) == 1:
        return wikis[0]
    if not wikis:
        typer.echo(f"lore: no wikis under {wiki_root}", err=True)
        raise typer.Exit(code=1)
    typer.echo(
        "lore: multiple wikis present — pass --wiki <name>: "
        + ", ".join(p.name for p in wikis),
        err=True,
    )
    raise typer.Exit(code=1)


def _is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _confirm(prompt: str, *, default: bool = False) -> bool:
    """y/n prompt. Non-interactive → return ``default``."""
    if not _is_interactive():
        return default
    suffix = " [y/N]" if not default else " [Y/n]"
    try:
        ans = input(prompt + suffix + " ").strip().lower()
    except EOFError:
        return default
    if not ans:
        return default
    return ans.startswith("y")


# ---------------------------------------------------------------------------
# `lore plan list`
# ---------------------------------------------------------------------------


@app.command("list")
def cmd_list(
    wiki: str = typer.Option(None, "--wiki", help="Wiki name (default: only-wiki)."),
    repo: str = typer.Option(None, "--repo", help="Filter by repo slug (org/name)."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON envelope."),
) -> None:
    """List active plans, ranked by recency."""
    from lore_core.plans.registry import list_active

    wiki_root = _resolve_wiki(wiki)
    cards = list_active(wiki_root, repo=repo)

    if json_out:
        _emit_json({
            "schema": "lore.plan.list/1",
            "data": {
                "wiki": wiki_root.name,
                "repo": repo,
                "plans": [
                    {
                        "slug": c.slug,
                        "description": c.description,
                        "status": c.status,
                        "repo": c.repo,
                        "steps_total": c.steps_total,
                        "steps_done": c.steps_done,
                        "steps_in_progress": c.steps_in_progress,
                        "next_pending_step": c.next_pending_step(),
                        "last_reviewed": c.last_reviewed,
                    }
                    for c in cards
                ],
            },
        })
        return

    if not cards:
        scope_hint = f" for repo `{repo}`" if repo else ""
        print(f"(no active plans{scope_hint})")
        return
    for c in cards:
        line = f"{c.slug} · {c.steps_done}/{c.steps_total} done"
        if c.steps_in_progress:
            line += f" · in-progress: {', '.join(c.steps_in_progress)}"
        np = c.next_pending_step()
        if np:
            line += f" · next: {np}"
        if c.repo:
            line += f"  ({c.repo})"
        print(line)
        if c.description:
            print(f"  {c.description}")


# ---------------------------------------------------------------------------
# `lore plan delete`
# ---------------------------------------------------------------------------


@app.command("delete")
def cmd_delete(
    slug: str = typer.Argument(..., help="Plan slug to delete."),
    wiki: str = typer.Option(None, "--wiki", help="Wiki name (default: only-wiki)."),
    force: bool = typer.Option(
        False, "--force", help="Skip the active-status confirm and incoming-link refuse."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON envelope."),
) -> None:
    """Delete a plan note. Refuses if other notes wikilink to it (use --force)."""
    from lore_core.plans.registry import read_one, scan_incoming_wikilinks
    from lore_core.plans.writer import plan_path

    wiki_root = _resolve_wiki(wiki)
    target = plan_path(wiki_root, slug)
    if not target.exists():
        typer.echo(f"lore: plan not found: {slug}", err=True)
        raise typer.Exit(code=1)

    card = read_one(wiki_root, slug)

    if not force:
        # Refuse on incoming wikilinks to avoid silent rug-pulls.
        incoming = scan_incoming_wikilinks(wiki_root, slug)
        if incoming:
            typer.echo(
                f"lore: refusing — {len(incoming)} note(s) wikilink to plan/{slug}; "
                "rerun with --force to delete anyway:",
                err=True,
            )
            for p in incoming:
                typer.echo(f"  - {p}", err=True)
            raise typer.Exit(code=2)

        # Confirm only when status is active (cleanup of done/abandoned is friction-free).
        if card and card.status == "active":
            if not _confirm(
                f"Delete active plan {slug}? This cannot be undone (use git to recover).",
                default=False,
            ):
                typer.echo("aborted", err=True)
                raise typer.Exit(code=1)

    target.unlink()
    msg = f"deleted {target}"
    hint = (
        f"commit when ready: cd {wiki_root.parent.parent} && "
        f"git add -A && git commit -m 'drop plan {slug}'"
    )
    if json_out:
        _emit_json({
            "schema": "lore.plan.delete/1",
            "data": {"slug": slug, "path": str(target), "commit_hint": hint},
        })
    else:
        print(msg)
        print(hint)


# ---------------------------------------------------------------------------
# `lore plan import`
# ---------------------------------------------------------------------------


@app.command("import")
def cmd_import(
    path: Path = typer.Argument(..., help="Path to orphan JSON envelope or markdown plan."),
    from_orphan: bool = typer.Option(
        False, "--from-orphan", help="Treat <path> as a JSON orphan envelope (recovery)."
    ),
    from_markdown: bool = typer.Option(
        False,
        "--from-markdown",
        help="Treat <path> as raw plan markdown (e.g. ~/.claude/plans/foo.md).",
    ),
    wiki: str = typer.Option(None, "--wiki"),
    repo: str = typer.Option(None, "--repo"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Import a plan from an orphan dump (JSON envelope) or raw markdown.

    Bare invocation (neither flag) dispatches by file extension:

    * ``.json`` → ``--from-orphan``
    * ``.md`` / ``.markdown`` → ``--from-markdown``
    * anything else → hard error (use one of the explicit flags)
    """
    if from_orphan and from_markdown:
        typer.echo("lore: pass at most one of --from-orphan / --from-markdown", err=True)
        raise typer.Exit(code=2)

    if not from_orphan and not from_markdown:
        suffix = path.suffix.lower()
        if suffix == ".json":
            from_orphan = True
        elif suffix in (".md", ".markdown"):
            from_markdown = True
        else:
            typer.echo(
                f"lore: ambiguous file type {suffix!r}; pass --from-orphan or --from-markdown",
                err=True,
            )
            raise typer.Exit(code=2)

    if not path.exists():
        typer.echo(f"lore: not found: {path}", err=True)
        raise typer.Exit(code=1)

    if from_orphan:
        plan_text = _extract_plan_from_orphan(path)
    else:
        plan_text = path.read_text()

    from lore_core.plans.parser import parse
    from lore_core.plans.writer import compute_source_hash, write_plan_note

    wiki_root = _resolve_wiki(wiki)
    plan = parse(plan_text)
    result = write_plan_note(
        wiki_root=wiki_root,
        plan=plan,
        source_hash=compute_source_hash(plan_text),
        source_adapter="manual-import" if from_markdown else "claude-code-orphan",
        repo=repo,
    )
    if json_out:
        _emit_json({
            "schema": "lore.plan.import/1",
            "data": {
                "slug": result.slug,
                "path": str(result.path),
                "outcome": result.outcome,
                "step_count": result.step_count,
            },
        })
    else:
        print(f"{result.outcome}: {result.path}")


def _extract_plan_from_orphan(path: Path) -> str:
    """Read the orphan JSON envelope and pull the plan markdown out."""
    from lore_core.plans.parser import parse_payload

    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        typer.echo(f"lore: orphan JSON malformed: {e}", err=True)
        raise typer.Exit(code=1)
    plan_text, source_field = parse_payload(payload)
    if plan_text is None:
        typer.echo(
            f"lore: no plan markdown found in orphan payload (source_field={source_field})",
            err=True,
        )
        raise typer.Exit(code=1)
    return plan_text


# ---------------------------------------------------------------------------
# `lore plan step`
# ---------------------------------------------------------------------------


@app.command("step")
def cmd_step(
    slug: str = typer.Argument(..., help="Plan slug."),
    step_id: str = typer.Argument(..., help="Step ID (e.g. s2)."),
    done: bool = typer.Option(False, "--done", help="Mark step as done."),
    in_progress: bool = typer.Option(
        False, "--in-progress", help="Mark step as in_progress."
    ),
    blocked: bool = typer.Option(False, "--blocked", help="Mark step as blocked."),
    pending: bool = typer.Option(
        False, "--pending", help="Clear status (move step back to pending)."
    ),
    wiki: str = typer.Option(None, "--wiki"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Set ``step_status[<step_id>]`` atomically."""
    flags = sum(int(x) for x in (done, in_progress, blocked, pending))
    if flags != 1:
        print(
            "lore: pass exactly one of --done / --in-progress / --blocked / --pending",
            file=sys.stderr,
        )
        raise typer.Exit(code=2)

    from lore_core.plans.step_status import set_step
    from lore_core.plans.types import StepStatus

    if done:
        new_status: StepStatus | None = StepStatus.DONE
    elif in_progress:
        new_status = StepStatus.IN_PROGRESS
    elif blocked:
        new_status = StepStatus.BLOCKED
    else:
        new_status = None

    wiki_root = _resolve_wiki(wiki)
    try:
        update = set_step(
            wiki_root=wiki_root, slug=slug, step_id=step_id, status=new_status
        )
    except (FileNotFoundError, ValueError) as e:
        typer.echo(f"lore: {e}", err=True)
        raise typer.Exit(code=1)

    if json_out:
        _emit_json({
            "schema": "lore.plan.step/1",
            "data": {
                "slug": update.slug,
                "step_id": update.step_id,
                "previous": update.previous,
                "current": update.current,
                "step_status_updated": update.bumped_timestamp,
            },
        })
    else:
        prev = update.previous or "pending"
        curr = update.current or "pending"
        print(f"{slug} · {step_id}: {prev} → {curr}")


# ---------------------------------------------------------------------------
# `lore plan advance`
# ---------------------------------------------------------------------------


@app.command("advance")
def cmd_advance(
    slug: str = typer.Argument(..., help="Plan slug."),
    wiki: str = typer.Option(None, "--wiki"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Mark the current in-progress step done; else mark the next pending step done."""
    from lore_core.plans.step_status import advance

    wiki_root = _resolve_wiki(wiki)
    try:
        update = advance(wiki_root=wiki_root, slug=slug)
    except FileNotFoundError as e:
        typer.echo(f"lore: {e}", err=True)
        raise typer.Exit(code=1)
    if update is None:
        msg = f"{slug}: nothing to advance — all steps are done."
        if json_out:
            _emit_json({"schema": "lore.plan.advance/1", "data": {"slug": slug, "advanced": False}})
        else:
            print(msg)
        return
    if json_out:
        _emit_json({
            "schema": "lore.plan.advance/1",
            "data": {
                "slug": update.slug,
                "step_id": update.step_id,
                "previous": update.previous,
                "current": update.current,
                "advanced": True,
            },
        })
    else:
        prev = update.previous or "pending"
        print(f"{slug} · {update.step_id}: {prev} → done")


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
