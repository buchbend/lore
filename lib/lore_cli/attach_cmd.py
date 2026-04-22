"""`lore attach` — accept, decline, register, or offer a Lore attachment.

Five commands exercise the state-machine:

* ``lore attach accept``  — accept the `.lore.yml` offer covering cwd
* ``lore attach decline`` — record a decline (fingerprint-keyed)
* ``lore attach manual``  — register an attachment without an offer
* ``lore attach offer``   — write a `.lore.yml` declaring a shareable offer

The file also exposes :func:`remove_section` used by the migration tool
and by ``lore detach`` to strip legacy ``## Lore`` CLAUDE.md sections.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import typer
from rich.console import Console

from lore_cli._compat import argv_main

console = Console()
err_console = Console(stderr=True)

from lore_core.attach import _split_lines, find_section


def _join_lines(lines: list[str], trailing: bool) -> str:
    text = "\n".join(lines)
    if trailing and not text.endswith("\n"):
        text += "\n"
    return text


def remove_section(path: Path) -> bool:
    """Remove the `## Lore` section from a CLAUDE.md. Returns True if
    something changed.

    Kept post-Phase-6 because the migration tool and ``lore detach``
    both need to strip legacy sections. Lives here (not in
    ``lore_core``) because it writes, and writers in ``lore_core`` are
    policy-bounded to state files only.
    """
    if not path.exists():
        return False
    lines, trailing = _split_lines(path.read_text())
    bounds = find_section(lines)
    if bounds is None:
        return False
    start, end = bounds

    # Drop the section. Also collapse one blank line before it so we
    # don't leave a double gap where the section used to be.
    cut_start = start
    if cut_start > 0 and lines[cut_start - 1].strip() == "":
        cut_start -= 1
    new_lines = lines[:cut_start] + lines[end:]

    # Trim any trailing whitespace-only tail.
    while new_lines and new_lines[-1].strip() == "":
        new_lines.pop()

    text = _join_lines(new_lines, trailing if new_lines else False)
    path.write_text(text)
    return True


def _resolve_claude_md(path_arg: str) -> Path:
    """Resolve the CLAUDE.md file for a given path argument.

    If ``path_arg`` points at a directory, append CLAUDE.md. If it points
    directly at a file, use it as-is.
    """
    p = Path(path_arg).expanduser().resolve()
    if p.is_dir() or not p.suffix:
        return p / "CLAUDE.md"
    return p


app = typer.Typer(
    add_completion=False,
    help=__doc__,
    no_args_is_help=True,
    rich_markup_mode="rich",
)


# ---- Registry-based commands (Phase 3 onwards) ----

def _lore_root_or_die() -> Path:
    env = os.environ.get("LORE_ROOT")
    if not env:
        err_console.print("[red]LORE_ROOT is not set.[/red]")
        raise typer.Exit(1)
    return Path(env)


def _cwd_arg(cwd_opt: str | None) -> Path:
    return Path(cwd_opt).expanduser() if cwd_opt else Path.cwd()


@app.command("accept")
def cmd_accept(
    cwd: str = typer.Option(None, "--cwd", help="Directory containing `.lore.yml` (default: current dir)."),
) -> None:
    """Accept the `.lore.yml` offer covering ``cwd``.

    Walks up from ``cwd`` looking for ``.lore.yml``; on the happy path,
    writes an attachment row (path = repo root, wiki/scope from the
    offer) and ingests the scope chain into ``scopes.json``.

    Exits 1 with a helpful message on any failure:
      * no ``.lore.yml`` found
      * scope root conflicts with an existing assignment (``lore scopes``
        to resolve, or decline and re-offer with a different root)
      * offer was previously declined (re-run after the `.lore.yml`
        changes, or remove the decline manually)
    """
    from datetime import UTC, datetime

    from lore_core.consent import ConsentState, classify_state
    from lore_core.offer import find_lore_yml, offer_fingerprint, parse_lore_yml
    from lore_core.state.attachments import Attachment, AttachmentsFile
    from lore_core.state.scopes import ScopeConflict, ScopesFile

    lore_root = _lore_root_or_die()
    cwd_path = _cwd_arg(cwd)

    offer_path = find_lore_yml(cwd_path)
    if offer_path is None:
        err_console.print(
            f"[red]No .lore.yml found at or above[/red] {cwd_path}.\n"
            "Use `lore attach manual --wiki ... --scope ...` for a repo "
            "without a checked-in offer."
        )
        raise typer.Exit(1)

    offer = parse_lore_yml(offer_path)
    if offer is None:
        err_console.print(f"[red]Could not parse[/red] {offer_path}")
        raise typer.Exit(1)

    repo_root = offer_path.parent
    fp = offer_fingerprint(offer)

    attachments = AttachmentsFile(lore_root)
    attachments.load()
    scopes = ScopesFile(lore_root)
    scopes.load()

    state = classify_state(cwd_path, attachments).state

    if state is ConsentState.DORMANT:
        err_console.print(
            "[yellow]This offer was previously declined.[/yellow] "
            "Accept anyway with `lore attach accept --cwd <path>` after "
            "removing the decline, or wait until the `.lore.yml` changes."
        )
        raise typer.Exit(1)

    # Ingest scope chain (backfills parents). May conflict at root.
    try:
        scopes.ingest_chain(offer.scope, offer.wiki)
    except ScopeConflict as exc:
        err_console.print(
            f"[red]Scope conflict:[/red] {exc}\n"
            f"Options:\n"
            f"  * Decline this offer: `lore attach decline --cwd {cwd_path}`\n"
            f"  * Rename the existing root before accepting: "
            f"`lore scopes rename {exc.scope_root} <new-root>`\n"
            f"  * Ask the repo maintainer to change the offer's scope."
        )
        raise typer.Exit(1)

    attachment = Attachment(
        path=repo_root,
        wiki=offer.wiki,
        scope=offer.scope,
        attached_at=datetime.now(UTC),
        source="accepted-offer",
        offer_fingerprint=fp,
    )
    attachments.add(attachment)
    attachments.save()
    scopes.save()

    console.print(
        f"[green]Attached[/green] {repo_root} → wiki [cyan]{offer.wiki}[/cyan], "
        f"scope [magenta]{offer.scope}[/magenta]"
    )


@app.command("decline")
def cmd_decline(
    cwd: str = typer.Option(None, "--cwd", help="Directory containing `.lore.yml` (default: current dir)."),
) -> None:
    """Decline the `.lore.yml` offer covering ``cwd``.

    Records a ``declined`` row keyed by (repo_root, offer_fingerprint).
    The SessionStart prompt will not re-offer this fingerprint. If the
    ``.lore.yml`` is later changed, the new fingerprint is not covered
    by the decline and will re-prompt.
    """
    from lore_core.offer import find_lore_yml, offer_fingerprint, parse_lore_yml
    from lore_core.state.attachments import AttachmentsFile

    lore_root = _lore_root_or_die()
    cwd_path = _cwd_arg(cwd)

    offer_path = find_lore_yml(cwd_path)
    if offer_path is None:
        err_console.print(f"[red]No .lore.yml found at or above[/red] {cwd_path}")
        raise typer.Exit(1)

    offer = parse_lore_yml(offer_path)
    if offer is None:
        err_console.print(f"[red]Could not parse[/red] {offer_path}")
        raise typer.Exit(1)

    repo_root = offer_path.parent
    fp = offer_fingerprint(offer)

    attachments = AttachmentsFile(lore_root)
    attachments.load()
    attachments.decline(repo_root, fp)
    attachments.save()

    console.print(
        f"[yellow]Declined[/yellow] offer for {repo_root} (wiki [cyan]{offer.wiki}[/cyan])."
    )


@app.command("manual")
def cmd_manual(
    wiki: str = typer.Option(..., "--wiki", help="Wiki name."),
    scope: str = typer.Option(..., "--scope", help="Scope ID (colon-separated)."),
    cwd: str = typer.Option(None, "--cwd", help="Directory to attach (default: current dir)."),
) -> None:
    """Attach ``cwd`` manually with no ``.lore.yml`` required.

    Writes an attachment row directly and ingests the scope chain. No
    offer fingerprint; no consent flow. Use this for repos without a
    checked-in offer.
    """
    from datetime import UTC, datetime

    from lore_core.state.attachments import Attachment, AttachmentsFile
    from lore_core.state.scopes import ScopeConflict, ScopesFile

    lore_root = _lore_root_or_die()
    cwd_path = _cwd_arg(cwd).resolve() if _cwd_arg(cwd).exists() else _cwd_arg(cwd).absolute()

    attachments = AttachmentsFile(lore_root)
    attachments.load()
    scopes = ScopesFile(lore_root)
    scopes.load()

    try:
        scopes.ingest_chain(scope, wiki)
    except ScopeConflict as exc:
        err_console.print(f"[red]Scope conflict:[/red] {exc}")
        raise typer.Exit(1)

    attachment = Attachment(
        path=cwd_path,
        wiki=wiki,
        scope=scope,
        attached_at=datetime.now(UTC),
        source="manual",
        offer_fingerprint=None,
    )
    attachments.add(attachment)
    attachments.save()
    scopes.save()

    console.print(
        f"[green]Attached[/green] {cwd_path} → wiki [cyan]{wiki}[/cyan], "
        f"scope [magenta]{scope}[/magenta] (manual)"
    )


@app.command("offer")
def cmd_offer(
    wiki: str = typer.Option(..., "--wiki", help="Wiki name for the offer."),
    scope: str = typer.Option(..., "--scope", help="Scope ID (colon-separated)."),
    cwd: str = typer.Option(None, "--cwd", help="Directory to write `.lore.yml` into (default: current dir)."),
    wiki_source: str = typer.Option(None, "--wiki-source", help="Optional URL for clone-on-accept."),
    backend: str = typer.Option("none", "--backend", help="github|none."),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing `.lore.yml`."),
) -> None:
    """Write a ``.lore.yml`` at ``cwd`` declaring a shareable offer.

    Does *not* attach this host — run ``lore attach accept`` after this
    to accept the just-written offer on the author's own machine.
    """
    import yaml

    from lore_core.offer import FILENAME

    cwd_path = _cwd_arg(cwd)
    if not cwd_path.exists():
        err_console.print(f"[red]Directory does not exist:[/red] {cwd_path}")
        raise typer.Exit(1)
    if not cwd_path.is_dir():
        err_console.print(f"[red]Not a directory:[/red] {cwd_path}")
        raise typer.Exit(1)

    target = cwd_path / FILENAME
    if target.exists() and not force:
        err_console.print(
            f"[red]{target} already exists.[/red] Pass --force to overwrite."
        )
        raise typer.Exit(1)

    payload: dict = {"wiki": wiki, "scope": scope, "backend": backend}
    if wiki_source:
        payload["wiki_source"] = wiki_source

    target.write_text(yaml.safe_dump(payload, sort_keys=False))
    console.print(
        f"[green]Wrote offer[/green] {target}\n"
        f"Run `lore attach accept --cwd {cwd_path}` to accept on this host."
    )


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
