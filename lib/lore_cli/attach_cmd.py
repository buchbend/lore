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

import sys
from dataclasses import dataclass
from pathlib import Path

import typer
from rich.console import Console

from lore_cli._argv_compat import argv_main

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
    from lore_cli._cli_helpers import lore_root_or_die
    return lore_root_or_die(err_console)


def _cwd_arg(cwd_opt: str | None) -> Path:
    return Path(cwd_opt).expanduser() if cwd_opt else Path.cwd()


# ---- Extracted helpers (shared by subcommands + wizard) ----

def _no_applicable_offer_message(cwd_path: Path) -> None:
    """Print a diagnostic explaining why no offer applies to ``cwd_path``.

    Distinguishes "no file anywhere" from "file at ancestor without
    ``inherit: true``" so users hit by the migration get a clear hint.
    """
    from lore_core.offer import find_lore_yml_raw

    raw = find_lore_yml_raw(cwd_path)
    if raw is None:
        err_console.print(
            f"[red]No .lore.yml found at or above[/red] {cwd_path}.\n"
            "Use `lore attach manual --wiki ... --scope ...` for a repo "
            "without a checked-in offer."
        )
        return
    err_console.print(
        f"[red]No applicable .lore.yml for[/red] {cwd_path}.\n"
        f"Found one at {raw}, but it does not apply to this directory.\n"
        f"  * Add `inherit: true` to {raw} to make it apply to descendants, or\n"
        f"  * Run `lore attach` from {raw.parent}, or\n"
        f"  * Use `lore attach manual --wiki ... --scope ...` for a separate config."
    )


def _do_accept(lore_root: Path, cwd_path: Path) -> None:
    from datetime import UTC, datetime

    from lore_core.consent import ConsentState, classify_state
    from lore_core.offer import find_lore_yml, offer_fingerprint
    from lore_core.state.attachments import Attachment, AttachmentsFile
    from lore_core.state.scopes import ScopeConflict, ScopesFile

    found = find_lore_yml(cwd_path)
    if found is None:
        _no_applicable_offer_message(cwd_path)
        raise typer.Exit(1)
    offer_path, offer = found

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
    stub = _maybe_stub_project_note(
        lore_root=lore_root,
        wiki=offer.wiki,
        repo_root=repo_root,
        scope=offer.scope,
    )
    _print_post_attach_guidance(lore_root, offer.wiki, stub=stub)


def _do_decline(lore_root: Path, cwd_path: Path) -> None:
    from lore_core.offer import find_lore_yml, offer_fingerprint
    from lore_core.state.attachments import AttachmentsFile

    found = find_lore_yml(cwd_path)
    if found is None:
        _no_applicable_offer_message(cwd_path)
        raise typer.Exit(1)
    offer_path, offer = found

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
    stub = _maybe_stub_project_note(
        lore_root=lore_root,
        wiki=wiki,
        repo_root=cwd_path,
        scope=scope,
    )
    _print_post_attach_guidance(lore_root, wiki, stub=stub)


def _stamp_offer_fingerprint(
    lore_root: Path, attachment_path: Path, offer_path: Path,
) -> None:
    """Sync the just-attached row's fingerprint to a freshly-written
    ``.lore.yml``.

    Without this, the row created by :func:`_do_manual` carries
    ``offer_fingerprint=None``, so the next SessionStart sees
    ``fp(offer-on-disk) ≠ None`` and reports DRIFT — telling the user the
    offer has "changed since you attached" even though the wizard wrote
    the offer milliseconds ago. Best-effort: a parse failure leaves the
    row alone so DRIFT can surface the bad file.
    """
    from lore_core.offer import offer_fingerprint, parse_lore_yml
    from lore_core.state.attachments import Attachment, AttachmentsFile

    offer = parse_lore_yml(offer_path)
    if offer is None:
        return
    fp = offer_fingerprint(offer)

    af = AttachmentsFile(lore_root)
    af.load()
    existing = af.get(attachment_path)
    if existing is None:
        return
    af.add(Attachment(
        path=existing.path,
        wiki=existing.wiki,
        scope=existing.scope,
        attached_at=existing.attached_at,
        source="accepted-offer",
        offer_fingerprint=fp,
    ))
    af.save()


def _print_post_attach_guidance(
    lore_root: Path, wiki: str, *, stub: "StubOutcome | None" = None
) -> None:
    from lore_core.config import get_wiki_root
    try:
        wiki_sessions = get_wiki_root() / wiki / "sessions"
    except Exception:
        wiki_sessions = lore_root / "wiki" / wiki / "sessions"
    console.print()
    console.print("  [dim]What happens now:[/dim]")
    console.print("  [dim]* Future sessions here will be captured automatically[/dim]")
    if stub is not None:
        if stub.new_link:
            # Quiet sub-bullet: only printed when a NEW project note was
            # stubbed. Idempotent re-stubs emit nothing — the user
            # already knows the note exists.
            console.print(
                f"  [dim]* Auto-stubbed project note:[/dim] [[{stub.new_link}]]"
            )
        elif stub.error_kind:
            # Visible-but-quiet failure signal — not a crash, but the
            # user (or operator running lore doctor) gets a thread to
            # pull. Beats silent "nothing happened."
            console.print(
                f"  [dim]* Project-note stub skipped ({stub.error_kind}); "
                "see lore doctor[/dim]"
            )
    console.print(f"  [dim]* Notes will appear in[/dim] {wiki_sessions}/")
    console.print("  [dim]* Historical sessions are not processed[/dim] (run [cyan]lore backfill[/cyan] to import past work)")
    console.print()
    console.print("  [dim]Verify: start a new Claude Code session, then[/dim] [cyan]lore status[/cyan]")


@dataclass(frozen=True)
class AncestorSuggestion:
    """Pre-fillable wiki/scope derived from an attached ancestor directory.

    When the user runs `lore attach` inside a child of an already-attached
    directory (e.g. a repo under ``~/orgs/ccat/`` where ``~/orgs/ccat/`` is
    attached as wiki=ccat, scope=ccat), we propose ``{ancestor.scope}:{cwd.name}``
    so the child gets a sensible nested scope by default instead of starting
    from a blank slate.
    """

    wiki: str
    scope: str
    ancestor_path: Path
    ancestor_scope: str


@dataclass(frozen=True)
class StubOutcome:
    """Three states `_print_post_attach_guidance` distinguishes.

    Without this distinction a stub failure was silent: ``None`` could
    mean either "note already exists, refreshed in place" or "stub
    generator crashed." Operators couldn't tell whether to investigate.
    """

    new_link: str | None = None  # set when a NEW note was created
    error_kind: str | None = None  # set on real exception (defensive path)


def _maybe_stub_project_note(
    *,
    lore_root: Path,
    wiki: str,
    repo_root: Path,
    scope: str | None,
) -> StubOutcome:
    """Auto-stub a project note for the just-attached repo. Best-effort.

    Returns a :class:`StubOutcome` so the caller can distinguish
    idempotent no-op from actual failure — silent failure on attach
    is exactly the kind of bug nobody discovers until the
    project-note feature appears broken weeks later.
    """
    try:
        from lore_core.config import get_wiki_root
        from lore_core.git import current_repo
        from lore_core.projects.stub_generator import stub_project_note

        try:
            wiki_root = get_wiki_root() / wiki
        except Exception:
            wiki_root = lore_root / "wiki" / wiki

        repo_slug = current_repo(repo_root)
        if not repo_slug:
            # Repo doesn't have a recognizable origin; fall back to dir name.
            repo_slug = repo_root.name

        result = stub_project_note(
            wiki_root=wiki_root,
            repo_root=repo_root,
            repo_slug=repo_slug,
            scope=scope,
        )
        if result.was_new:
            return StubOutcome(new_link=result.path.stem)
        return StubOutcome()  # idempotent no-op
    except Exception as exc:  # noqa: BLE001 — never fail attach on stub error
        return StubOutcome(error_kind=type(exc).__name__)


# ---- Interactive wizard ----

def _is_interactive() -> bool:
    return sys.stdin.isatty()


def _ancestor_attachment_suggestion(
    cwd: Path,
    attachments: "AttachmentsFile",  # noqa: F821 — forward ref, imported lazily
) -> AncestorSuggestion | None:
    """Walk up from ``cwd`` and propose a child scope under the closest
    attached ancestor directory.

    Returns ``None`` when no strict ancestor (parent or higher) is attached.
    The suggestion uses ``{ancestor.scope}:{cwd.name}``; the leaf segment
    is just the directory name, not anything derived from a git remote —
    keeps the wizard offline and matches what the user can see in their
    shell prompt.
    """
    parent = cwd.parent
    if parent == cwd:
        return None
    match = attachments.longest_prefix_match(parent)
    if match is None:
        return None
    return AncestorSuggestion(
        wiki=match.wiki,
        scope=f"{match.scope}:{cwd.name}",
        ancestor_path=match.path,
        ancestor_scope=match.scope,
    )

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


def _execute_attach(
    lore_root: Path,
    resolved: Path,
    *,
    wiki: str,
    scope: str,
    backend: str,
    write_offer: bool,
) -> None:
    """Shared executor: register the attachment, optionally write a
    ``.lore.yml`` and stamp its fingerprint onto the row so the very
    next session doesn't see it as DRIFT."""
    from lore_core.offer import FILENAME

    _do_manual(lore_root, resolved, wiki, scope)

    if write_offer:
        import yaml
        target = resolved / FILENAME
        payload: dict = {"wiki": wiki, "scope": scope, "backend": backend}
        target.write_text(yaml.safe_dump(payload, sort_keys=False))
        console.print(f"[green]Wrote[/green] {target}")
        _stamp_offer_fingerprint(lore_root, resolved, target)


def _config_wizard(
    cwd_path: Path,
    lore_root: Path,
    *,
    defaults: object | None = None,  # lore_core.offer.Offer | None
    ancestor_suggestion: AncestorSuggestion | None = None,
) -> None:
    from lore_core.config import get_wiki_root
    from lore_core.offer import FILENAME
    from lore_core.state.scopes import ScopesFile

    wiki_root = get_wiki_root()
    wikis = sorted(d.name for d in wiki_root.iterdir() if d.is_dir()) if wiki_root.exists() else []
    resolved = cwd_path.resolve() if cwd_path.exists() else cwd_path.absolute()

    # One-click accept: when the parent suggested a full config, present
    # it as a single A/s/c prompt before drilling through each field.
    # Most attachments to a child of an attached parent want the obvious
    # defaults (wiki=parent.wiki, scope=parent.scope:dirname, backend=github,
    # write .lore.yml so other contributors / other hosts inherit cleanly).
    # Skip when an offer's defaults won — `_config_detected_flow` already
    # offers [u]se as-is for that case.
    if ancestor_suggestion is not None and defaults is None:
        proposed_wiki = ancestor_suggestion.wiki
        proposed_scope = ancestor_suggestion.scope
        proposed_backend = "github"
        proposed_write = not (cwd_path / FILENAME).exists()

        console.print(
            f"\n[bold]Proposed config[/bold] (from parent attachment "
            f"{ancestor_suggestion.ancestor_path}):"
        )
        console.print(f"  Wiki:       [cyan]{proposed_wiki}[/cyan]")
        console.print(f"  Scope:      [magenta]{proposed_scope}[/magenta]")
        console.print(f"  Backend:    {proposed_backend}")
        if proposed_write:
            console.print(f"  .lore.yml:  will be written")
        console.print()
        choice = input("  [A]ccept   [s]tep through   [c]ancel: ").strip().lower()
        if choice in ("", "a", "y", "yes", "accept"):
            _execute_attach(
                lore_root, resolved,
                wiki=proposed_wiki, scope=proposed_scope,
                backend=proposed_backend, write_offer=proposed_write,
            )
            return
        if choice in ("c", "n", "no", "cancel", "abort"):
            console.print("[yellow]Aborted.[/yellow]")
            raise typer.Exit(0)
        # Anything else (notably "s") falls through to step-through.
        console.print("\n[dim]Stepping through fields…[/dim]")

    # Step A: Wiki
    default_wiki = (
        defaults.wiki if defaults
        else (ancestor_suggestion.wiki if ancestor_suggestion else None)
    )
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

    default_scope: str | None = None
    if defaults:
        default_scope = defaults.scope
    elif ancestor_suggestion is not None and ancestor_suggestion.wiki == wiki:
        default_scope = ancestor_suggestion.scope
        # Prepend so the suggestion is selectable as choice [1] even
        # when it isn't already a registered scope ID.
        if default_scope not in matching:
            matching = [default_scope, *matching]

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

    # Step C: Backend (default github — covers >99% of attached repos;
    # offer-driven flows still inherit the offer's choice).
    default_backend = defaults.backend if defaults else "github"
    raw = input(f"\n  Backend [github/none] ({default_backend}): ").strip().lower()
    backend = raw if raw in ("github", "none") else default_backend

    # Step D: Write .lore.yml for other contributors?
    # Exact-cwd existence check — we only need to know whether we'd
    # be overwriting a local file. Inheriting parent offers don't
    # block writing a child override; the user is here precisely to
    # configure this directory.
    write_offer = False
    if not (cwd_path / FILENAME).exists():
        raw = input("\n  Write .lore.yml so other contributors get this config? [y/N]: ").strip().lower()
        write_offer = raw in ("y", "yes")

    # Step E: Summary + confirm
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

    _execute_attach(
        lore_root, resolved,
        wiki=wiki, scope=scope, backend=backend, write_offer=write_offer,
    )


def _interactive_wizard(cwd_path: Path, lore_root: Path) -> None:
    from lore_core.offer import find_lore_yml
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

    ancestor_suggestion = _ancestor_attachment_suggestion(resolved, attachments)

    found = find_lore_yml(cwd_path)
    if found is not None:
        offer_path, offer = found
        if offer_path.parent != resolved:
            console.print(f"\n[dim]Inherited from[/dim] {offer_path}")
        _config_detected_flow(offer, offer_path, cwd_path, lore_root)
        return

    _config_wizard(cwd_path, lore_root, ancestor_suggestion=ancestor_suggestion)


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
