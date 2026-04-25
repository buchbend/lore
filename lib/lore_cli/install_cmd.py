"""`lore install` — multi-host installer dispatcher.

Subcommands:
  lore install                    interactive — install for every detected host
  lore install --host claude      one host
  lore install --host all         every host where the binary is on PATH
  lore install check [--host …]   plan-only, never writes
  lore install upgrade [--host …] re-install: no-op if matching schema
  lore install uninstall [--host …]  symmetric semantic remove
  lore uninstall                  alias for `lore install uninstall`

Flags:
  --yes      non-interactive (kind=replace still prompts)
  --quiet    suppress per-action lines; just the final ✓/✗ summary
  --json     structured output on stdout (best-effort, no schema commitment in v1)
  --force    proceed despite legacy install.sh artifacts (rejected with --yes)

UX contract (per the four-pass plan review):
  • One prompt per host with inline action list (apt-style, not npm-style)
  • [d] keypress expands the diffs
  • kind=replace always prompts even with --yes
  • Pre-pipx latency hint before subprocess'ing installers
  • Success sentence names the verification step + next-plan handoff
  • Failure cross-links to `lore doctor`
  • markup=False on Rich for any user-derived string (path, settings content)
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import typer
from rich.console import Console
from rich.markup import escape as rich_escape

from lore_runtime.argv import argv_main
from lore_core.install import REGISTRY, known_hosts
from lore_core.install._helpers import (
    detect_install_sh_artifacts,
    execute_action,
    preview_action,
)
from lore_core.install.base import (
    KIND_REPLACE,
    KIND_RUN,
    Action,
    ApplyResult,
    InstallContext,
    LegacyArtifact,
)

console = Console()


# ---------------------------------------------------------------------------
# Host filtering
# ---------------------------------------------------------------------------


def _binary_for(host_name: str) -> str:
    """Map host name → expected binary on PATH."""
    return {"claude": "claude", "cursor": "cursor"}.get(host_name, host_name)


def _select_hosts(arg: str | None, *, interactive: bool = False) -> list[str]:
    """Resolve --host into a concrete list of host names.

    None or "all" → every host whose binary is on PATH.
    A specific name → that host (no PATH check).

    When *interactive* is True and no --host flag was given, present a
    numbered list and let the user choose which hosts to install for.
    """
    all_hosts = known_hosts()
    if arg is not None and arg != "all":
        if arg not in all_hosts:
            raise SystemExit(
                f"lore install: unknown host '{arg}' "
                f"(known: {', '.join(all_hosts)})"
            )
        return [arg]

    detected = [h for h in all_hosts if shutil.which(_binary_for(h))]

    if not interactive:
        return detected if detected else all_hosts

    # --- Interactive tool selection ---
    if not detected:
        console.print(
            "\n[yellow]No supported tools detected on PATH.[/yellow]",
            markup=True,
        )
        console.print("  Supported hosts:", markup=False)
        for i, h in enumerate(all_hosts, 1):
            console.print(f"    [{i}] {h}", markup=False)
        ans = input(
            f"  Install for which? (comma-separated numbers, or 'all') [{', '.join(str(i) for i in range(1, len(all_hosts) + 1))}]: "
        ).strip()
        if not ans or ans.lower() == "all":
            return all_hosts
        chosen = _parse_host_selection(ans, all_hosts)
        return chosen if chosen else all_hosts

    if len(detected) == 1:
        ans = input(f"\n  Install for {detected[0]}? [Y/n]: ").strip().lower()
        if ans in ("n", "no"):
            return []
        return detected

    # Multiple detected
    console.print("\n[bold]Detected tools:[/bold]", markup=True)
    for i, h in enumerate(detected, 1):
        console.print(f"    [{i}] {h}", markup=False)
    ans = input(
        f"  Install for which? (comma-separated numbers, or 'all') [all]: "
    ).strip()
    if not ans or ans.lower() == "all":
        return detected
    chosen = _parse_host_selection(ans, detected)
    return chosen if chosen else detected


def _parse_host_selection(ans: str, hosts: list[str]) -> list[str]:
    """Parse a comma-separated list of 1-based indices into host names."""
    chosen: list[str] = []
    for part in ans.split(","):
        part = part.strip()
        if part.isdigit():
            idx = int(part) - 1
            if 0 <= idx < len(hosts):
                chosen.append(hosts[idx])
    return chosen


# ---------------------------------------------------------------------------
# Plan rendering
# ---------------------------------------------------------------------------


def _render_action_line(action: Action) -> str:
    """One short line per action, leading with the kind verb."""
    return f"  {action.kind:7} {action.target} — {action.summary}"


def _render_action_diff(action: Action) -> str:
    """Multi-line diff/preview for the [d] expansion."""
    return f"\n{preview_action(action)}\n"


_LEGACY_LABELS = {
    "skill_symlink": ("skill symlink", "skill symlinks"),
    "agent_symlink": ("agent symlink", "agent symlinks"),
    "hook_entry": (
        "hook entry in ~/.claude/settings.json",
        "hook entries in ~/.claude/settings.json",
    ),
    "permission_rule": (
        "permission rule in ~/.claude/settings.json",
        "permission rules in ~/.claude/settings.json",
    ),
    "env_entry": (
        "env entry in ~/.claude/settings.json",
        "env entries in ~/.claude/settings.json",
    ),
}


def _print_legacy_warning(artifacts: list[LegacyArtifact]) -> None:
    grouped: dict[str, list[str]] = {}
    for a in artifacts:
        grouped.setdefault(a.kind, []).append(a.detail)
    console.print(
        "[yellow]\u26a0 Detected legacy install.sh artifacts:[/yellow]",
        markup=True,
    )
    for kind, items in grouped.items():
        n = len(items)
        singular, plural = _LEGACY_LABELS.get(kind, (kind, f"{kind}s"))
        console.print(f"    {n} {singular if n == 1 else plural}", markup=False)
    console.print()
    console.print(
        "  Run [cyan]python3 tools/undo_install_sh.py[/cyan] first, "
        "then re-run [cyan]lore install[/cyan].",
        markup=True,
    )
    console.print(
        "  Override: [cyan]--force[/cyan] (not allowed in combination "
        "with [cyan]--yes[/cyan]).",
        markup=True,
    )


def _print_host_plan(host_name: str, actions: list[Action]) -> None:
    if not actions:
        console.print(
            f"\n[bold]Lore for {host_name}:[/bold] nothing to do — already current.",
            markup=True,
        )
        return
    targets = sorted({a.target for a in actions})
    console.print(
        f"\n[bold]About to install Lore for {host_name}[/bold] — touching "
        f"{', '.join(targets)}",
        markup=True,
    )
    console.print()
    for a in actions:
        console.print(_render_action_line(a), markup=False)


# ---------------------------------------------------------------------------
# Prompt logic
# ---------------------------------------------------------------------------


def _prompt_host(host_name: str, actions: list[Action], yes: bool) -> str:
    """Return 'y' (proceed), 'n' (skip), or 'd' (diff and re-prompt).

    With --yes: returns 'y' immediately UNLESS any action is kind=replace,
    in which case still prompts (per-action prompts handled inside
    _execute_actions).
    """
    if yes:
        return "y"
    while True:
        ans = input("\n  Proceed? [Y/n/d] (d = show full diffs) ").strip().lower()
        if ans in ("", "y", "yes"):
            return "y"
        if ans in ("n", "no"):
            return "n"
        if ans in ("d", "diff"):
            console.print()
            for a in actions:
                console.print(_render_action_diff(a), markup=False)
            continue
        console.print(
            "[yellow]please answer Y, n, or d[/yellow]", markup=True
        )


def _prompt_replace(action: Action) -> bool:
    """Per-action confirm for kind=replace. Returns True to proceed."""
    console.print(
        f"\n  [yellow]\u26a0 replace[/yellow] {action.target} — "
        f"{action.summary}",
        markup=True,
    )
    console.print(_render_action_diff(action), markup=False)
    while True:
        ans = input("  Replace? [y/N] ").strip().lower()
        if ans in ("y", "yes"):
            return True
        if ans in ("", "n", "no"):
            return False
        console.print(
            "[yellow]please answer y or N[/yellow]", markup=True
        )


# ---------------------------------------------------------------------------
# Execution + reporting
# ---------------------------------------------------------------------------


def _execute_actions(
    actions: list[Action], yes: bool, quiet: bool
) -> tuple[list[ApplyResult], int]:
    """Execute each action in order. Returns (results, fail_count)."""
    results: list[ApplyResult] = []
    fail_count = 0
    for a in actions:
        # kind=replace always prompts even with --yes
        if a.kind == KIND_REPLACE and not _prompt_replace(a):
            results.append(
                ApplyResult(ok=False, error="declined by user")
            )
            fail_count += 1
            if a.on_failure == "abort_host":
                break
            continue
        # Pre-pipx latency hint for subprocess kinds
        if a.kind == KIND_RUN and not quiet:
            argv = a.payload.get("argv") or []
            if argv and argv[0] in ("pipx", "uv", "pip", "claude"):
                console.print(
                    f"  [dim]running {argv[0]} (~10–60s)…[/dim]",
                    markup=True,
                )
        result = execute_action(a)
        results.append(result)
        if not quiet:
            mark = "[green]\u2713[/green]" if result.ok else "[red]\u2717[/red]"
            # Escape user-derived strings (paths) but keep wrapper markup.
            console.print(
                f"  {mark} {a.kind:7} {rich_escape(a.target)}",
                markup=True,
            )
            if not result.ok and result.error:
                # Wrapper colour stays markup; the error body itself is
                # escaped to prevent ANSI injection from subprocess output.
                console.print(
                    f"    [red]{rich_escape(result.error)}[/red]",
                    markup=True,
                )
        if not result.ok:
            fail_count += 1
            if a.on_failure == "abort_host":
                break
    return results, fail_count


_SUCCESS_HOST_SENTENCE = {
    "claude": (
        "Done. Open a Claude Code session and run /lore:context to verify."
    ),
    "cursor": (
        "Done. Restart Cursor and open the MCP tools panel; you should "
        "see lore_search and 8 others."
    ),
}


def _print_host_summary(host_name: str, fail_count: int, mode: str) -> None:
    if fail_count == 0:
        msg = _SUCCESS_HOST_SENTENCE.get(host_name, "Done.")
        if mode == "uninstall":
            msg = "Uninstalled."
        console.print(f"\n  [green]\u2713[/green] {host_name}: {msg}", markup=True)
    else:
        console.print(
            f"\n  [red]\u2717[/red] {host_name}: {fail_count} action(s) "
            "failed. Run [cyan]lore doctor[/cyan] to diagnose. Capture "
            "state with: [cyan]lore doctor --json > lore-debug.json[/cyan]",
            markup=True,
        )


# ---------------------------------------------------------------------------
# Mode handlers
# ---------------------------------------------------------------------------


def _build_ctx(args: SimpleNamespace) -> InstallContext:
    return InstallContext(
        lore_repo=Path(args.lore_repo).expanduser() if args.lore_repo else None,
        force=args.force,
        dry_run=args.cmd == "check",
    )


def _emit_json(envelope: dict) -> None:
    print(json.dumps(envelope, indent=2, default=str))


def _is_interactive(args: SimpleNamespace) -> bool:
    """True when the session is interactive (tty, no automation flags)."""
    return sys.stdin.isatty() and not args.yes and not args.json and not args.quiet


def _post_install_setup() -> None:
    """Interactive vault + wiki scaffolding after a successful host install.

    Only called in interactive mode for fresh installs. Skipped silently
    if the vault already exists and has wikis.
    """
    from lore_core.config import get_lore_root

    # --- Vault setup ---------------------------------------------------
    try:
        lore_root = get_lore_root()
        has_vault = (lore_root / "wiki").is_dir()
    except Exception:
        has_vault = False

    if has_vault:
        wiki_root = lore_root / "wiki"
        existing_wikis = [
            d.name for d in wiki_root.iterdir() if d.is_dir()
        ]
        if existing_wikis:
            # Vault exists and has wikis — nothing to do
            return

    console.print()
    console.print("[bold]Vault setup[/bold]", markup=True)

    default_root = Path.home() / "lore"
    vault_input = input(
        f"  Where to store your vault? [{default_root}]: "
    ).strip()
    vault_path = Path(vault_input).expanduser().resolve() if vault_input else default_root

    from lore_cli.init_cmd import init_vault  # lazy to avoid circular imports

    init_vault(vault_path, force=False)

    # --- Wiki setup ----------------------------------------------------
    wiki_root = vault_path / "wiki"
    existing_wikis = (
        [d.name for d in wiki_root.iterdir() if d.is_dir()]
        if wiki_root.exists()
        else []
    )

    if not existing_wikis:
        console.print()
        console.print("  Do you have a team wiki to connect?")
        console.print("    [g] Clone from GitHub URL")
        console.print("    [f] Link existing folder")
        console.print("    [s] Skip — create a personal wiki only")
        team_choice = input("  Choice [s]: ").strip().lower() or "s"

        if team_choice == "g":
            url = input("  GitHub URL: ").strip()
            if url:
                import subprocess as _sp

                wiki_name = (
                    url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
                )
                target = wiki_root / wiki_name
                _sp.run(["git", "clone", url, str(target)], check=True)
                console.print(
                    f"  [green]✓[/green] Team wiki '{rich_escape(wiki_name)}' cloned",
                    markup=True,
                )
        elif team_choice == "f":
            folder = input("  Folder path: ").strip()
            if folder:
                source = Path(folder).expanduser().resolve()
                wiki_name = source.name
                target = wiki_root / wiki_name
                target.symlink_to(source)
                console.print(
                    f"  [green]✓[/green] Wiki '{rich_escape(wiki_name)}' linked",
                    markup=True,
                )

        # Personal wiki
        personal = input("  Create a personal wiki too? [Y/n]: ").strip().lower()
        if personal != "n":
            name = input("  Personal wiki name [personal]: ").strip() or "personal"
            from lore_cli.new_wiki_cmd import scaffold_wiki  # lazy import

            scaffold_wiki(name, mode="personal")
            console.print(
                f"  [green]✓[/green] Wiki '{rich_escape(name)}' created",
                markup=True,
            )

    # --- Final handoff -------------------------------------------------
    console.print()
    console.print(
        "  [bold]Next:[/bold] run [cyan]lore attach[/cyan] in any repo "
        "to start capturing sessions.",
        markup=True,
    )
    console.print(
        "  [bold]Verify:[/bold] [cyan]lore doctor[/cyan]",
        markup=True,
    )


def _cmd_install(args: SimpleNamespace, mode: str = "install") -> int:
    """Shared install / upgrade / uninstall driver. `mode` selects:
        install   → host.plan(ctx)
        upgrade   → host.plan(ctx) (same; the dispatcher reports no-op
                    when all actions are no-op kind=check)
        uninstall → host.uninstall_plan(ctx)
    """
    if args.force and args.yes:
        console.print(
            "[red]Combining --force with --yes is not allowed.[/red]\n"
            "If you want CI to bulldoze legacy state, run:\n"
            "  [cyan]python3 tools/undo_install_sh.py --yes && "
            "lore install --yes[/cyan]",
            markup=True,
        )
        return 2

    interactive = _is_interactive(args)
    hosts = _select_hosts(
        args.host, interactive=(interactive and mode == "install")
    )
    if not hosts:
        console.print("  [yellow]No hosts selected.[/yellow]", markup=True)
        return 0
    ctx = _build_ctx(args)

    # Legacy artifact detection — only for install / upgrade (and only
    # gates writing modes; `check` shows everything and exits 0).
    # Uninstall operates regardless — you may be uninstalling legacy
    # state.
    legacy_artifacts: list[LegacyArtifact] = []
    if mode != "uninstall":
        legacy_artifacts = detect_install_sh_artifacts(lore_repo=ctx.lore_repo)
        if legacy_artifacts:
            # In --json mode, omit the human warning (it'd contaminate
            # stdout). The artifacts ride in the JSON envelope below.
            if not args.json:
                _print_legacy_warning(legacy_artifacts)
            # Check mode: print plan too, then exit 0
            if args.cmd != "check" and not args.force:
                if args.json:
                    _emit_json(
                        {
                            "ok": False,
                            "reason": "legacy_artifacts",
                            "artifacts": [a.__dict__ for a in legacy_artifacts],
                        }
                    )
                return 1

    # Build per-host plan
    plans: list[tuple[str, list[Action]]] = []
    for host_name in hosts:
        host = REGISTRY[host_name]
        if mode == "uninstall":
            actions = host.uninstall_plan(ctx)
        else:
            actions = host.plan(ctx)
        plans.append((host_name, actions))

    # JSON output mode — emit the plan envelope and exit
    if args.json or args.cmd == "check":
        envelope = {
            "mode": mode,
            "legacy_artifacts": [a.__dict__ for a in legacy_artifacts],
            "hosts": [
                {
                    "host": name,
                    "actions": [a.to_dict() for a in actions],
                }
                for name, actions in plans
            ],
        }
        if args.json:
            _emit_json(envelope)
        else:
            for name, actions in plans:
                _print_host_plan(name, actions)
        return 0

    # Interactive / --yes path
    overall_failures = 0
    for name, actions in plans:
        _print_host_plan(name, actions)
        if not actions:
            continue
        choice = _prompt_host(name, actions, args.yes)
        if choice == "n":
            console.print(f"\n  [yellow]skipped {name}[/yellow]", markup=True)
            continue
        if not args.quiet:
            console.print()  # blank line before per-action ✓ lines
        results, fail_count = _execute_actions(
            actions, yes=args.yes, quiet=args.quiet
        )
        overall_failures += fail_count
        _print_host_summary(name, fail_count, mode)

    # Final handoff
    if mode == "install" and overall_failures == 0:
        if interactive:
            try:
                _post_install_setup()
            except (KeyboardInterrupt, EOFError):
                console.print(
                    "\n\n  [yellow]Setup interrupted.[/yellow] "
                    "Run [cyan]lore init[/cyan] later to finish vault setup.",
                    markup=True,
                )
        else:
            console.print(
                "\n[bold]Next:[/bold] run [cyan]lore init[/cyan] to scaffold "
                "your vault.",
                markup=True,
            )
    return 0 if overall_failures == 0 else 1


app = typer.Typer(
    add_completion=False,
    help=__doc__,
    no_args_is_help=False,
    rich_markup_mode="rich",
)


def _make_args(
    cmd: str,
    *,
    host: str | None,
    yes: bool,
    quiet: bool,
    json_out: bool,
    force: bool,
    lore_repo: str | None,
) -> SimpleNamespace:
    """Adapt typer kwargs into the argparse-Namespace shape `_cmd_install` reads."""
    return SimpleNamespace(
        cmd=cmd,
        host=host,
        yes=yes,
        quiet=quiet,
        json=json_out,
        force=force,
        lore_repo=lore_repo,
    )


def _exit_with(rc: int) -> None:
    if rc:
        raise typer.Exit(code=rc)


# Common flag set repeated across subcommands. Typer doesn't share
# options between root + subcommands cleanly (Click constraint), so
# we declare them on each function. ~6 lines × 4 commands.

_HOST = typer.Option(
    None, "--host", help="Host to install for (claude|cursor|all). Default: all detected."
)
_YES = typer.Option(
    False, "--yes", "-y", help="Non-interactive; assume Y to all non-replace prompts."
)
_QUIET = typer.Option(
    False, "--quiet", "-q", help="Suppress per-action output; just the final summary."
)
_JSON = typer.Option(
    False, "--json", help="Emit a structured plan/result envelope on stdout."
)
_FORCE = typer.Option(
    False,
    "--force",
    help="Proceed despite legacy install.sh artifacts. Rejected if combined with --yes.",
)
_LORE_REPO = typer.Option(
    None, "--lore-repo", help="Path to a lore source checkout (for editable / dev installs)."
)


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    host: str = _HOST,
    yes: bool = _YES,
    quiet: bool = _QUIET,
    json_out: bool = _JSON,
    force: bool = _FORCE,
    lore_repo: str = _LORE_REPO,
) -> None:
    """Default action — install Lore for one or more hosts."""
    if ctx.invoked_subcommand is not None:
        return  # the subcommand handles its own work
    args = _make_args(
        "install",
        host=host,
        yes=yes,
        quiet=quiet,
        json_out=json_out,
        force=force,
        lore_repo=lore_repo,
    )
    _exit_with(_cmd_install(args, mode="install"))


@app.command("check")
def cmd_check(
    host: str = _HOST,
    yes: bool = _YES,
    quiet: bool = _QUIET,
    json_out: bool = _JSON,
    force: bool = _FORCE,
    lore_repo: str = _LORE_REPO,
) -> None:
    """Plan-only — never writes."""
    args = _make_args(
        "check",
        host=host,
        yes=yes,
        quiet=quiet,
        json_out=json_out,
        force=force,
        lore_repo=lore_repo,
    )
    _exit_with(_cmd_install(args, mode="install"))


@app.command("upgrade")
def cmd_upgrade(
    host: str = _HOST,
    yes: bool = _YES,
    quiet: bool = _QUIET,
    json_out: bool = _JSON,
    force: bool = _FORCE,
    lore_repo: str = _LORE_REPO,
) -> None:
    """Re-install — no-op if managed schema is current."""
    args = _make_args(
        "upgrade",
        host=host,
        yes=yes,
        quiet=quiet,
        json_out=json_out,
        force=force,
        lore_repo=lore_repo,
    )
    _exit_with(_cmd_install(args, mode="upgrade"))


@app.command("uninstall")
def cmd_uninstall(
    host: str = _HOST,
    yes: bool = _YES,
    quiet: bool = _QUIET,
    json_out: bool = _JSON,
    force: bool = _FORCE,
    lore_repo: str = _LORE_REPO,
) -> None:
    """Symmetric semantic remove."""
    args = _make_args(
        "uninstall",
        host=host,
        yes=yes,
        quiet=quiet,
        json_out=json_out,
        force=force,
        lore_repo=lore_repo,
    )
    _exit_with(_cmd_install(args, mode="uninstall"))


@app.command("reinstall")
def cmd_reinstall(
    host: str = _HOST,
    yes: bool = _YES,
    quiet: bool = _QUIET,
    json_out: bool = _JSON,
    force: bool = _FORCE,
    lore_repo: str = _LORE_REPO,
) -> None:
    """Uninstall then install — useful after upgrading the Lore package.

    Equivalent to:

        lore install uninstall && lore install

    Pair with ``claude plugin update lore@lore`` to force Claude's
    plugin cache to re-fetch (the ``.claude-plugin/plugin.json``
    version must be bumped in the repo for the update to do anything
    — see CHANGELOG.md).
    """
    uninstall_args = _make_args(
        "reinstall",
        host=host,
        yes=yes,
        quiet=quiet,
        json_out=json_out,
        force=force,
        lore_repo=lore_repo,
    )
    rc = _cmd_install(uninstall_args, mode="uninstall")
    if rc != 0:
        _exit_with(rc)

    install_args = _make_args(
        "reinstall",
        host=host,
        yes=yes,
        quiet=quiet,
        json_out=json_out,
        force=force,
        lore_repo=lore_repo,
    )
    rc = _cmd_install(install_args, mode="install")

    if rc == 0 and not json_out and not quiet:
        from rich.console import Console
        Console().print(
            "\n[dim]Next:[/dim] run [bold]claude plugin update lore@lore[/bold] "
            "(or restart Claude) to re-fetch the plugin cache."
        )
    _exit_with(rc)


main = argv_main(app)


# ---------------------------------------------------------------------------
# `lore uninstall` shim — uses the same dispatcher with mode=uninstall
# ---------------------------------------------------------------------------


def uninstall_main(argv: list[str] | None = None) -> int:
    """Entry point for the `lore uninstall` alias."""
    if argv is None:
        argv = sys.argv[1:]
    return main(["uninstall", *argv])


if __name__ == "__main__":
    sys.exit(main())
