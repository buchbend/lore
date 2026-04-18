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

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from rich.console import Console

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


def _select_hosts(arg: str | None) -> list[str]:
    """Resolve --host into a concrete list of host names.

    None or "all" → every host whose binary is on PATH.
    A specific name → that host (no PATH check).
    """
    all_hosts = known_hosts()
    if arg is None or arg == "all":
        present = [h for h in all_hosts if shutil.which(_binary_for(h))]
        if not present:
            return all_hosts  # fall back to all known if nothing detected
        return present
    if arg not in all_hosts:
        raise SystemExit(
            f"lore install: unknown host '{arg}' "
            f"(known: {', '.join(all_hosts)})"
        )
    return [arg]


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
            console.print(
                f"  {mark} {a.kind:7} {a.target}",
                markup=True,
            )
            if not result.ok and result.error:
                console.print(f"    [red]{result.error}[/red]", markup=False)
        if not result.ok:
            fail_count += 1
            if a.on_failure == "abort_host":
                break
    return results, fail_count


_SUCCESS_HOST_SENTENCE = {
    "claude": (
        "Done. Open a Claude Code session and run /lore:loaded to verify."
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


def _build_ctx(args: argparse.Namespace) -> InstallContext:
    return InstallContext(
        lore_repo=Path(args.lore_repo).expanduser() if args.lore_repo else None,
        force=args.force,
        dry_run=args.cmd == "check",
    )


def _emit_json(envelope: dict) -> None:
    print(json.dumps(envelope, indent=2, default=str))


def _cmd_install(args: argparse.Namespace, mode: str = "install") -> int:
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

    hosts = _select_hosts(args.host)
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
        console.print(
            "\n[bold]Next:[/bold] run [cyan]lore init[/cyan] to scaffold "
            "your vault.",
            markup=True,
        )
    return 0 if overall_failures == 0 else 1


def _cmd_check(args: argparse.Namespace) -> int:
    return _cmd_install(args, mode="install")  # check path branches inside


def _cmd_upgrade(args: argparse.Namespace) -> int:
    return _cmd_install(args, mode="upgrade")


def _cmd_uninstall(args: argparse.Namespace) -> int:
    return _cmd_install(args, mode="uninstall")


# ---------------------------------------------------------------------------
# argparse plumbing
# ---------------------------------------------------------------------------


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--host",
        default=None,
        help="Host to install for (claude|cursor|all). Default: all detected.",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Non-interactive; assume Y to all non-replace prompts.",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress per-action output; just the final summary.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a structured plan/result envelope on stdout.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Proceed despite legacy install.sh artifacts. "
        "Rejected if combined with --yes.",
    )
    parser.add_argument(
        "--lore-repo",
        default=None,
        help="Path to a lore source checkout (for editable / dev installs).",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lore-install", description=__doc__)
    subs = parser.add_subparsers(dest="cmd")

    p_install = subs.add_parser(
        "install", help="Install Lore for one or more hosts (default mode)"
    )
    _add_common(p_install)
    p_install.set_defaults(func=lambda a: _cmd_install(a))

    p_check = subs.add_parser("check", help="Plan-only; never writes")
    _add_common(p_check)
    p_check.set_defaults(func=_cmd_check)

    p_upgrade = subs.add_parser(
        "upgrade", help="Re-install: no-op if matching schema"
    )
    _add_common(p_upgrade)
    p_upgrade.set_defaults(func=_cmd_upgrade)

    p_uninstall = subs.add_parser(
        "uninstall", help="Symmetric semantic remove"
    )
    _add_common(p_uninstall)
    p_uninstall.set_defaults(func=_cmd_uninstall)

    # No subcommand → default to `install`
    if not argv:
        argv = sys.argv[1:]
    if not argv or argv[0].startswith("-"):
        argv = ["install", *argv]

    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help(sys.stderr)
        return 2
    return int(args.func(args) or 0)


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
