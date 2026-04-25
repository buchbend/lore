"""`lore attach` — accept, decline, register, or offer a Lore attachment.

Five commands exercise the state-machine:

* ``lore attach accept``  — accept the `.lore.yml` offer covering cwd
* ``lore attach decline`` — record a decline (fingerprint-keyed)
* ``lore attach manual``  — register an attachment without an offer
* ``lore attach offer``   — write a `.lore.yml` declaring a shareable offer

Running bare ``lore attach`` (no subcommand) starts an interactive wizard.

The file also exposes :func:`remove_section` used by the migration tool
and by ``lore detach`` to strip legacy ``## Lore`` CLAUDE.md sections.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import typer
from rich.console import Console

from lore_runtime.argv import argv_main

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
    no_args_is_help=False,
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


# ---- Extracted helpers (shared by subcommands + wizard) ----

def _do_accept(lore_root: Path, cwd_path: Path) -> None:
    from datetime import UTC, datetime

    from lore_core.consent import ConsentState, classify_state
    from lore_core.offer import find_lore_yml, offer_fingerprint, parse_lore_yml
    from lore_core.state.attachments import Attachment, AttachmentsFile
    from lore_core.state.scopes import ScopeConflict, ScopesFile

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
    _print_post_attach_guidance(lore_root, offer.wiki)


def _do_decline(lore_root: Path, cwd_path: Path) -> None:
    from lore_core.offer import find_lore_yml, offer_fingerprint, parse_lore_yml
    from lore_core.state.attachments import AttachmentsFile

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


def _do_manual(lore_root: Path, cwd_path: Path, wiki: str, scope: str) -> None:
    from datetime import UTC, datetime

    from lore_core.state.attachments import Attachment, AttachmentsFile
    from lore_core.state.scopes import ScopeConflict, ScopesFile

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
    _print_post_attach_guidance(lore_root, wiki)


def _print_post_attach_guidance(lore_root: Path, wiki: str) -> None:
    from lore_core.config import get_wiki_root
    try:
        wiki_sessions = get_wiki_root() / wiki / "sessions"
    except Exception:
        wiki_sessions = lore_root / "wiki" / wiki / "sessions"
    console.print()
    console.print("  [dim]What happens now:[/dim]")
    console.print("  [dim]* Future sessions here will be captured automatically[/dim]")
    console.print(f"  [dim]* Notes will appear in[/dim] {wiki_sessions}/")
    console.print("  [dim]* Historical sessions are not processed[/dim] (run [cyan]lore backfill[/cyan] to import past work)")
    console.print()
    console.print("  [dim]Verify: start a new Claude Code session, then[/dim] [cyan]lore status[/cyan]")


# ---- Interactive wizard ----

def _is_interactive() -> bool:
    return sys.stdin.isatty()

def _pick_from_list(
    label: str,
    choices: list[str],
    *,
    default: str | None = None,
    allow_custom: bool = False,
) -> str:
    console.print(f"\n[bold]{label}:[/bold]")
    default_idx: int | None = None
    for i, choice in enumerate(choices, 1):
        marker = ""
        if default and choice == default:
            marker = "  [dim](default)[/dim]"
            default_idx = i
        console.print(f"  [cyan]\\[{i}][/cyan] {choice}{marker}")
    if allow_custom:
        console.print("  [cyan]\\[c][/cyan] custom name")

    prompt_hint = f" [{default_idx}]" if default_idx else ""
    while True:
        raw = input(f"  Choice{prompt_hint}: ").strip()
        if not raw and default_idx is not None:
            return choices[default_idx - 1]
        if raw.lower() == "c" and allow_custom:
            while True:
                custom = input("  Enter value: ").strip()
                if custom:
                    return custom
                console.print("  [red]Value cannot be empty.[/red]")
        try:
            idx = int(raw)
            if 1 <= idx <= len(choices):
                return choices[idx - 1]
        except ValueError:
            pass
        console.print(f"  [red]Invalid choice.[/red] Enter 1-{len(choices)}"
                       + (" or 'c'" if allow_custom else "") + ".")


def _config_detected_flow(
    offer: object,  # lore_core.offer.Offer
    offer_path: Path,
    cwd_path: Path,
    lore_root: Path,
) -> None:
    console.print(
        f"\n[bold]This repo has a Lore config[/bold] ({offer_path.name}):"
    )
    console.print(
        f"  wiki: [cyan]{offer.wiki}[/cyan]    "
        f"scope: [magenta]{offer.scope}[/magenta]    "
        f"backend: {offer.backend}"
    )
    console.print()
    console.print("  [cyan]\\[u][/cyan]se as-is   [cyan]\\[c][/cyan]ustomize   [cyan]\\[s][/cyan]kip")

    while True:
        raw = input("  Choice: ").strip().lower()
        if raw == "u":
            _do_accept(lore_root, cwd_path)
            return
        if raw == "c":
            _config_wizard(cwd_path, lore_root, defaults=offer)
            return
        if raw == "s":
            _do_decline(lore_root, cwd_path)
            return
        console.print("  [red]Invalid choice.[/red] Enter u, c, or s.")


def _config_wizard(
    cwd_path: Path,
    lore_root: Path,
    *,
    defaults: object | None = None,  # lore_core.offer.Offer | None
) -> None:
    from lore_core.config import get_wiki_root
    from lore_core.state.scopes import ScopesFile

    wiki_root = get_wiki_root()
    wikis = sorted(d.name for d in wiki_root.iterdir() if d.is_dir()) if wiki_root.exists() else []

    # Step A: Wiki
    default_wiki = defaults.wiki if defaults else None
    if wikis:
        wiki = _pick_from_list("Wiki", wikis, default=default_wiki, allow_custom=True)
    elif default_wiki:
        raw = input(f"\n  Wiki [{default_wiki}]: ").strip()
        wiki = raw if raw else default_wiki
    else:
        while True:
            wiki = input("\n  Wiki name: ").strip()
            if wiki:
                break
            console.print("  [red]Wiki name cannot be empty.[/red]")

    # Step B: Scope
    scopes = ScopesFile(lore_root)
    scopes.load()
    all_ids = scopes.all_ids()
    matching = [sid for sid in all_ids if scopes.resolve_wiki(sid) == wiki]
    default_scope = defaults.scope if defaults else None

    if matching:
        scope = _pick_from_list(
            f"Scope (wiki: {wiki})", matching,
            default=default_scope, allow_custom=True,
        )
    elif default_scope:
        raw = input(f"\n  Scope [{default_scope}]: ").strip()
        scope = raw if raw else default_scope
    else:
        while True:
            scope = input("\n  Scope (colon-separated, e.g. project:sub): ").strip()
            if scope:
                break
            console.print("  [red]Scope cannot be empty.[/red]")

    # Step C: Backend
    default_backend = defaults.backend if defaults else "none"
    raw = input(f"\n  Backend [github/none] ({default_backend}): ").strip().lower()
    backend = raw if raw in ("github", "none") else default_backend

    # Step D: Write .lore.yml for other contributors?
    from lore_core.offer import FILENAME, find_lore_yml
    write_offer = False
    if find_lore_yml(cwd_path) is None:
        raw = input("\n  Write .lore.yml so other contributors get this config? [y/N]: ").strip().lower()
        write_offer = raw in ("y", "yes")

    # Step E: Summary + confirm
    resolved = cwd_path.resolve() if cwd_path.exists() else cwd_path.absolute()
    console.print("\n[bold]─── Attach summary ───[/bold]")
    console.print(f"  Directory:  {resolved}")
    console.print(f"  Wiki:       [cyan]{wiki}[/cyan]")
    console.print(f"  Scope:      [magenta]{scope}[/magenta]")
    console.print(f"  Backend:    {backend}")
    if write_offer:
        console.print(f"  .lore.yml:  will be written")
    console.print()

    raw = input("  Proceed? [Y/n]: ").strip().lower()
    if raw in ("n", "no"):
        console.print("[yellow]Aborted.[/yellow]")
        raise typer.Exit(0)

    # Execute
    _do_manual(lore_root, resolved, wiki, scope)

    if write_offer:
        import yaml
        target = resolved / FILENAME
        payload: dict = {"wiki": wiki, "scope": scope, "backend": backend}
        target.write_text(yaml.safe_dump(payload, sort_keys=False))
        console.print(f"[green]Wrote[/green] {target}")


def _interactive_wizard(cwd_path: Path, lore_root: Path) -> None:
    from lore_core.offer import find_lore_yml, parse_lore_yml
    from lore_core.state.attachments import AttachmentsFile

    resolved = cwd_path.resolve() if cwd_path.exists() else cwd_path.absolute()
    attachments = AttachmentsFile(lore_root)
    attachments.load()
    existing = attachments.longest_prefix_match(resolved)

    if existing and existing.path == resolved:
        console.print(
            f"\n[yellow]Already attached:[/yellow] {existing.path} → "
            f"wiki [cyan]{existing.wiki}[/cyan], "
            f"scope [magenta]{existing.scope}[/magenta]"
        )
        raw = input("  Re-attach with new config? [y/N]: ").strip().lower()
        if raw not in ("y", "yes"):
            raise typer.Exit(0)
    elif existing:
        console.print(
            f"\n[dim]Covered by parent attachment:[/dim] {existing.path} → "
            f"wiki [cyan]{existing.wiki}[/cyan], "
            f"scope [magenta]{existing.scope}[/magenta]"
        )

    offer_path = find_lore_yml(cwd_path)
    if offer_path:
        offer = parse_lore_yml(offer_path)
        if offer:
            _config_detected_flow(offer, offer_path, cwd_path, lore_root)
            return

    _config_wizard(cwd_path, lore_root)


# ---- Interactive callback ----

@app.callback(invoke_without_command=True)
def attach_interactive(
    ctx: typer.Context,
    cwd: str = typer.Option(None, "--cwd", help="Working directory (default: current dir)."),
) -> None:
    """Interactive Lore attachment wizard."""
    if ctx.invoked_subcommand is not None:
        return
    if not _is_interactive():
        err_console.print("[red]Interactive wizard requires a terminal.[/red]")
        err_console.print("Use: lore attach manual --wiki ... --scope ...")
        raise typer.Exit(1)
    _interactive_wizard(_cwd_arg(cwd), _lore_root_or_die())


# ---- Subcommands (thin wrappers over extracted helpers) ----

@app.command("accept")
def cmd_accept(
    cwd: str = typer.Option(None, "--cwd", help="Directory containing `.lore.yml` (default: current dir)."),
) -> None:
    """Accept the `.lore.yml` offer covering ``cwd``."""
    _do_accept(_lore_root_or_die(), _cwd_arg(cwd))


@app.command("decline")
def cmd_decline(
    cwd: str = typer.Option(None, "--cwd", help="Directory containing `.lore.yml` (default: current dir)."),
) -> None:
    """Decline the `.lore.yml` offer covering ``cwd``."""
    _do_decline(_lore_root_or_die(), _cwd_arg(cwd))


@app.command("manual")
def cmd_manual(
    wiki: str = typer.Option(..., "--wiki", help="Wiki name."),
    scope: str = typer.Option(..., "--scope", help="Scope ID (colon-separated)."),
    cwd: str = typer.Option(None, "--cwd", help="Directory to attach (default: current dir)."),
) -> None:
    """Attach ``cwd`` manually with no ``.lore.yml`` required."""
    cwd_path = _cwd_arg(cwd)
    resolved = cwd_path.resolve() if cwd_path.exists() else cwd_path.absolute()
    _do_manual(_lore_root_or_die(), resolved, wiki, scope)


@app.command("offer")
def cmd_offer(
    wiki: str = typer.Option(..., "--wiki", help="Wiki name for the offer."),
    scope: str = typer.Option(..., "--scope", help="Scope ID (colon-separated)."),
    cwd: str = typer.Option(None, "--cwd", help="Directory to write `.lore.yml` into (default: current dir)."),
    wiki_source: str = typer.Option(None, "--wiki-source", help="Optional URL for clone-on-accept."),
    backend: str = typer.Option("none", "--backend", help="github|none."),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing `.lore.yml`."),
) -> None:
    """Write a ``.lore.yml`` at ``cwd`` declaring a shareable offer."""
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
