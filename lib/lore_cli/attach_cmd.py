"""`lore attach` — read/write the managed `## Lore` section in CLAUDE.md.

Parses and upserts a small key-value block without touching any content
outside the `## Lore` heading. See the concept note
`claude-md-as-scope-anchor` in the private wiki for the contract.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from rich.console import Console

console = Console()
err_console = Console(stderr=True)

# Read-side parsing lives in lore_core.attach so non-CLI modules
# (e.g. lore_core.scope_resolver) don't need to depend on lore_cli.
# Re-exported here for backward compatibility.
from lore_core.attach import (  # noqa: F401  (re-exports)
    BULLET_RE,
    HEADING_RE,
    LORE_KEYS,
    SECTION_HEADING,
    _split_lines,
    find_section,
    parse_section_body,
    read_attach,
)

MANAGED_COMMENT = (
    "<!-- Managed by /lore:attach. "
    "Safe to edit — changes are preserved on re-run. -->"
)


def _join_lines(lines: list[str], trailing: bool) -> str:
    text = "\n".join(lines)
    if trailing and not text.endswith("\n"):
        text += "\n"
    return text


def _render_fresh_section() -> list[str]:
    return [
        SECTION_HEADING,
        "",
        MANAGED_COMMENT,
        "",
    ]


def _upsert_in_body(body_lines: list[str], updates: dict[str, str]) -> list[str]:
    """Return new body lines with Lore-owned keys upserted.

    Rules:
      - If a Lore-owned key already exists as a bullet, replace the value.
      - If absent, append at the end of the contiguous bullet group (or at
        section end if no bullets exist yet).
      - Non-bullet lines (comments, blank lines, prose) are preserved as-is.
      - User-added bullets (keys not in LORE_KEYS) are preserved.
    """
    new_body = list(body_lines)
    seen: set[str] = set()

    # Pass 1: replace in place.
    for i, line in enumerate(new_body):
        m = BULLET_RE.match(line)
        if not m:
            continue
        key = m.group(1)
        if key in updates and key in LORE_KEYS:
            new_body[i] = f"- {key}: {updates[key]}"
            seen.add(key)

    # Pass 2: append missing Lore keys at the tail of the last bullet run.
    missing = [k for k in LORE_KEYS if k in updates and k not in seen]
    if not missing:
        return new_body

    # Find insertion point: after the last existing bullet, else at end
    # skipping trailing blank lines.
    last_bullet = -1
    for i, line in enumerate(new_body):
        if BULLET_RE.match(line):
            last_bullet = i

    new_bullets = [f"- {k}: {updates[k]}" for k in missing]
    if last_bullet >= 0:
        insert_at = last_bullet + 1
        return new_body[:insert_at] + new_bullets + new_body[insert_at:]

    # No bullets yet — insert before trailing blank lines.
    insert_at = len(new_body)
    while insert_at > 0 and new_body[insert_at - 1].strip() == "":
        insert_at -= 1
    prefix = new_body[:insert_at]
    suffix = new_body[insert_at:]
    # Ensure a blank line separates the comment from the bullets.
    if prefix and prefix[-1].strip() != "":
        prefix = prefix + [""]
    return prefix + new_bullets + suffix


def write_attach(path: Path, updates: dict[str, str]) -> str:
    """Upsert the Lore section. Creates CLAUDE.md if absent. Returns text."""
    # Keep only Lore-owned keys in the update set — policy boundary.
    updates = {k: v for k, v in updates.items() if k in LORE_KEYS and v is not None}

    if not path.exists():
        new_body = _upsert_in_body([], updates)
        lines = _render_fresh_section() + new_body
        text = _join_lines(lines, trailing=True)
        path.write_text(text)
        return text

    lines, trailing = _split_lines(path.read_text())
    bounds = find_section(lines)

    if bounds is None:
        # Append section at end.
        prefix = list(lines)
        if prefix and prefix[-1].strip() != "":
            prefix.append("")
        fresh = _render_fresh_section()
        body = _upsert_in_body([], updates)
        new_lines = prefix + fresh + body
        text = _join_lines(new_lines, trailing=True)
        path.write_text(text)
        return text

    start, end = bounds
    body = lines[start + 1 : end]
    new_body = _upsert_in_body(body, updates)
    new_lines = lines[: start + 1] + new_body + lines[end:]
    text = _join_lines(new_lines, trailing or True)
    path.write_text(text)
    return text


def remove_section(path: Path) -> bool:
    """Remove the Lore section. Returns True if something changed."""
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

    If `path_arg` points at a directory, append CLAUDE.md. If it points
    directly at a file, use it as-is.
    """
    p = Path(path_arg).expanduser().resolve()
    if p.is_dir() or not p.suffix:
        return p / "CLAUDE.md"
    return p


import os  # noqa: E402

import typer  # noqa: E402

from lore_cli._compat import argv_main  # noqa: E402

app = typer.Typer(
    add_completion=False,
    help=__doc__,
    no_args_is_help=True,
    rich_markup_mode="rich",
)


@app.command("read")
def cmd_read(
    path: str = typer.Option(".", "--path", help="Folder or CLAUDE.md path."),
) -> None:
    """Print the parsed `## Lore` block as JSON.

    Legacy: reads from CLAUDE.md. For the registry path, use
    ``lore attachments show <path>``.
    """
    target = _resolve_claude_md(path)
    block = read_attach(target)
    envelope = {
        "schema": "lore.attach.read/1",
        "data": {"path": str(target), "block": block},
    }
    print(json.dumps(envelope, indent=2))


@app.command("write")
def cmd_write(
    wiki: str = typer.Option(..., "--wiki", help="Wiki name."),
    scope: str = typer.Option(..., "--scope", help="Scope path within the wiki."),
    path: str = typer.Option(".", "--path", help="Folder or CLAUDE.md path."),
    backend: str = typer.Option(
        None, "--backend", help="github|none (default: inferred)."
    ),
    issues: str = typer.Option(None, "--issues", help="gh issue list filter flags."),
    prs: str = typer.Option(None, "--prs", help="gh pr list filter flags."),
) -> None:
    """Upsert the managed ``## Lore`` section in CLAUDE.md (legacy path).

    Kept for back-compat during the Phase 3–5 transition. New code should
    use ``lore attach accept`` / ``lore attach manual``.
    """
    target = _resolve_claude_md(path)
    updates: dict[str, str] = {"wiki": wiki, "scope": scope}
    if backend is not None:
        updates["backend"] = backend
    if issues is not None:
        updates["issues"] = issues
    if prs is not None:
        updates["prs"] = prs
    write_attach(target, updates)
    result = read_attach(target)
    # Affordance for humans goes to stderr so stdout stays parseable JSON.
    err_console.print(f"[green]Attached {target}[/green]")
    envelope = {
        "schema": "lore.attach.write/1",
        "data": {"path": str(target), "block": result},
    }
    print(json.dumps(envelope, indent=2))


# ---- Registry-based commands (Phase 3) ----

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
