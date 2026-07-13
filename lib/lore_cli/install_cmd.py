"""`lore install` — multi-integration installer dispatcher.

Subcommands:
  lore install                              interactive — install for every detected integration
  lore install --integration claude         one integration
  lore install --integration all            every integration where the binary is on PATH
  lore install check [--integration …]      plan-only, never writes
  lore install upgrade [--integration …]    re-install: no-op if matching schema
  lore install uninstall [--integration …]  symmetric semantic remove
  lore uninstall                            alias for `lore install uninstall`

Flags:
  --yes      non-interactive (kind=replace still prompts)
  --quiet    suppress per-action lines; just the final ✓/✗ summary
  --json     structured output on stdout (best-effort, no schema commitment in v1)
  --force    proceed despite legacy install.sh artifacts (rejected with --yes)

UX contract (per the four-pass plan review):
  • One prompt per integration with inline action list (apt-style, not npm-style)
  • [d] keypress expands the diffs
  • kind=replace always prompts even with --yes
  • Pre-pipx latency hint before subprocess'ing installers
  • Success sentence names the verification step + next-plan handoff
  • Failure cross-links to `lore doctor`
  • markup=False on Rich for any user-derived string (path, settings content)
"""

from __future__ import annotations

import importlib.metadata
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import typer
from lore_core.install import REGISTRY, known_integrations
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
from rich.console import Console
from rich.markup import escape as rich_escape

from lore_cli._argv_compat import argv_main

console = Console()


# ---------------------------------------------------------------------------
# Integration filtering
# ---------------------------------------------------------------------------


def _binary_for(integration_name: str) -> str:
    """Map integration name → expected binary on PATH."""
    return {"claude": "claude", "cursor": "cursor"}.get(integration_name, integration_name)


def _integration_present(integration_name: str) -> bool:
    """True when the integration is installed on this host.

    Probe order:
      1. ``shutil.which(<binary>)`` — works for CLI-shipping installs
         (Claude Code, Cursor's macOS Homebrew flavor)
      2. Per-integration directory marker — catches GUI-only installs
         where no CLI is on PATH (Cursor on Linux .deb / .AppImage,
         macOS .dmg drag-install).
    """
    if shutil.which(_binary_for(integration_name)):
        return True
    if integration_name == "cursor":
        return (Path.home() / ".cursor").is_dir()
    return False


def _select_integrations(arg: str | None, *, interactive: bool = False) -> list[str]:
    """Resolve --integration into a concrete list of integration names.

    None or "all" → every integration detected on this host (CLI on
    PATH or per-integration directory marker — see ``_integration_present``).
    A specific name → that integration (no PATH check).

    When *interactive* is True and no --integration flag was given, present a
    numbered list and let the user choose which integrations to install for.
    """
    all_integrations = known_integrations()
    if arg is not None and arg != "all":
        if arg not in all_integrations:
            raise SystemExit(
                f"lore install: unknown integration '{arg}' "
                f"(known: {', '.join(all_integrations)})"
            )
        return [arg]

    detected = [h for h in all_integrations if _integration_present(h)]

    if not interactive:
        return detected if detected else all_integrations

    # --- Interactive tool selection ---
    if not detected:
        console.print(
            "\n[yellow]No supported tools detected on PATH.[/yellow]",
            markup=True,
        )
        console.print("  Supported integrations:", markup=False)
        for i, h in enumerate(all_integrations, 1):
            console.print(f"    [{i}] {h}", markup=False)
        ans = input(
            f"  Install for which? (comma-separated numbers, or 'all') "
            f"[{', '.join(str(i) for i in range(1, len(all_integrations) + 1))}]: "
        ).strip()
        if not ans or ans.lower() == "all":
            return all_integrations
        chosen = _parse_integration_selection(ans, all_integrations)
        return chosen if chosen else all_integrations

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
        "  Install for which? (comma-separated numbers, or 'all') [all]: "
    ).strip()
    if not ans or ans.lower() == "all":
        return detected
    chosen = _parse_integration_selection(ans, detected)
    return chosen if chosen else detected


def _parse_integration_selection(ans: str, integrations: list[str]) -> list[str]:
    """Parse a comma-separated list of 1-based indices into integration names."""
    chosen: list[str] = []
    for part in ans.split(","):
        part = part.strip()
        if part.isdigit():
            idx = int(part) - 1
            if 0 <= idx < len(integrations):
                chosen.append(integrations[idx])
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
        "[yellow]⚠ Detected legacy install.sh artifacts:[/yellow]",
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


def _print_integration_plan(integration_name: str, actions: list[Action]) -> None:
    if not actions:
        console.print(
            f"\n[bold]Lore for {integration_name}:[/bold] nothing to do — already current.",
            markup=True,
        )
        return
    targets = sorted({a.target for a in actions})
    console.print(
        f"\n[bold]About to install Lore for {integration_name}[/bold] — touching "
        f"{', '.join(targets)}",
        markup=True,
    )
    console.print()
    for a in actions:
        console.print(_render_action_line(a), markup=False)


# ---------------------------------------------------------------------------
# Prompt logic
# ---------------------------------------------------------------------------


def _prompt_integration(integration_name: str, actions: list[Action], yes: bool) -> str:
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
        f"\n  [yellow]⚠ replace[/yellow] {action.target} — "
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
            if a.on_failure == "abort_integration":
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
            mark = "[green]✓[/green]" if result.ok else "[red]✗[/red]"
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
            if a.on_failure == "abort_integration":
                break
    return results, fail_count


_SUCCESS_INTEGRATION_SENTENCE = {
    "claude": (
        "Done. Open a Claude Code session and run /lore:context to verify."
    ),
    "cursor": (
        "Done. Restart Cursor and open the MCP tools panel; you should "
        "see lore_search and 8 others."
    ),
}


def _print_integration_summary(integration_name: str, fail_count: int, mode: str) -> None:
    if fail_count == 0:
        msg = _SUCCESS_INTEGRATION_SENTENCE.get(integration_name, "Done.")
        if mode == "uninstall":
            msg = "Uninstalled."
        console.print(
            f"\n  [green]✓[/green] {integration_name}: {msg}", markup=True
        )
    else:
        console.print(
            f"\n  [red]✗[/red] {integration_name}: {fail_count} action(s) "
            "failed. Run [cyan]lore doctor[/cyan] to diagnose. Capture "
            "state with: [cyan]lore doctor --json > lore-debug.json[/cyan]",
            markup=True,
        )


# ---------------------------------------------------------------------------
# Mode handlers
# ---------------------------------------------------------------------------


def _build_ctx(args: SimpleNamespace, *, mode: str) -> InstallContext:
    return InstallContext(
        lore_repo=Path(args.lore_repo).expanduser() if args.lore_repo else None,
        force=args.force,
        dry_run=mode == "check",
    )


def _emit_json(envelope: dict) -> None:
    print(json.dumps(envelope, indent=2, default=str))


def _is_interactive(args: SimpleNamespace) -> bool:
    """True when the session is interactive (tty, no automation flags)."""
    return sys.stdin.isatty() and not args.yes and not args.json and not args.quiet


_INSTALL_SH_URL = "https://raw.githubusercontent.com/buchbend/lore/main/install.sh"


def _run_self_upgrade(*, quiet: bool) -> int:
    """Hand off to ``install.sh upgrade`` for a binary self-upgrade.

    The bash script is the source of truth for installation: it picks
    pipx / uv / pip, runs the upgrade, then chains into ``lore install``
    (the freshly-installed binary) to refresh integrations. Doing this
    via a subprocess — instead of mutating the running Python process —
    sidesteps the self-replacement problem (a process can't reliably
    upgrade the binary it's currently executing from).

    Resolution order for the script:
      1. ``./install.sh`` next to a source checkout (dev workflow)
      2. curl-fetch from the canonical URL on GitHub
    """
    if not quiet:
        console.print(
            "\n[dim]→ self-upgrade: handing off to "
            "[cyan]install.sh upgrade[/cyan]...[/dim]",
            markup=True,
        )

    # Prefer a local checkout if one is on disk (the source layout puts
    # install.sh two levels above this module: lib/lore_cli/install_cmd.py).
    local = Path(__file__).resolve().parents[2] / "install.sh"
    if local.is_file():
        cmd = ["bash", str(local), "upgrade"]
    elif shutil.which("curl") and shutil.which("bash"):
        cmd = [
            "bash",
            "-c",
            f"curl -fsSL {_INSTALL_SH_URL} | bash -s upgrade",
        ]
    else:
        console.print(
            "[red]Self-upgrade needs `curl` + `bash` on PATH, or a lore "
            "source checkout next to this module.[/red]\n"
            "  Manual fallback: [cyan]pipx install --force "
            "git+https://github.com/buchbend/lore.git[/cyan] then "
            "[cyan]claude plugin update lore@lore[/cyan].",
            markup=True,
        )
        return 1

    try:
        return subprocess.run(cmd, check=False).returncode
    except (OSError, KeyboardInterrupt) as exc:
        console.print(f"[red]install.sh failed: {exc}[/red]", markup=True)
        return 1


_REMOTE_PLUGIN_JSON_URL = (
    "https://raw.githubusercontent.com/buchbend/lore/main/.claude-plugin/plugin.json"
)


def _fetch_remote_version() -> str | None:
    """Latest released version, read off `plugin.json` on `main`.

    `pyproject.toml` and `.claude-plugin/plugin.json` are bumped together
    in every `chore: release X.Y.Z` commit, so this one file is enough to
    know the latest release without cloning the repo. Returns None on any
    network or parse failure — the caller decides how to report that.
    """
    try:
        with urllib.request.urlopen(_REMOTE_PLUGIN_JSON_URL, timeout=10) as resp:
            return json.loads(resp.read()).get("version")
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def _local_version() -> str:
    return importlib.metadata.version("lore")


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.strip().split("."))


def _remote_is_newer(remote: str, local: str) -> bool:
    """Compare dotted versions as int tuples — a plain string compare would
    put "0.9.0" after "0.10.0"."""
    r, loc = _version_tuple(remote), _version_tuple(local)
    n = max(len(r), len(loc))
    r += (0,) * (n - len(r))
    loc += (0,) * (n - len(loc))
    return r > loc


def _loud_plugin_cache_failure(detail: str) -> None:
    """Plugin-cache refresh failed — never silent.

    A stale cache means hooks, skills, and the MCP server keep serving the
    previous manifest with no error at all (exactly the silent-failure
    class this epic targets), so surface it loudly and name the manual
    fix plus the required Claude Code restart. Printed even under
    ``--quiet``: this is an error, not a per-action line.
    """
    from rich.panel import Panel

    console.print(
        Panel(
            "Claude's plugin cache was NOT refreshed:\n"
            f"  {detail}\n\n"
            "Hooks, skills, and the MCP server may keep serving the old "
            "version.\n"
            "If Lore isn't installed as a plugin yet, add it via /plugin.\n"
            "Otherwise fix it manually, then restart Claude Code:\n"
            "  claude plugin update lore@lore",
            title="plugin cache stale — restart Claude Code",
            border_style="red",
            expand=False,
        ),
        markup=False,
    )


def _refresh_claude_plugin_cache(*, quiet: bool) -> None:
    """Run `claude plugin update lore@lore` so Claude re-fetches the manifest.

    The Claude plugin cache is keyed on the `version` in
    `.claude-plugin/plugin.json`; without this refresh, installed plugins
    keep serving the previous manifest (skills, hooks config, MCP server
    registration) until the user runs the update by hand. We invoke it as
    part of `lore install` so the Claude-side install is one step.

    Failures are non-fatal: `claude` may not be on PATH (Code-only setup),
    the plugin may not yet be installed via `/plugin add`, or the update
    may report up-to-date. In each case we print a short hint and return.
    """
    claude_bin = shutil.which("claude")
    if claude_bin is None:
        if not quiet:
            console.print(
                "\n[dim]Note:[/dim] [bold]claude[/bold] CLI not on PATH — "
                "run [cyan]claude plugin update lore@lore[/cyan] manually "
                "once Claude Code is installed.",
                markup=True,
            )
        return

    if not quiet:
        console.print(
            "\n[dim]→ refreshing Claude plugin cache "
            "([cyan]claude plugin update lore@lore[/cyan])...[/dim]",
            markup=True,
        )
    try:
        result = subprocess.run(
            [claude_bin, "plugin", "update", "lore@lore"],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _loud_plugin_cache_failure(str(exc))
        return

    if result.returncode != 0:
        stderr = (result.stderr or result.stdout or "").strip().splitlines()
        tail = stderr[-1] if stderr else f"exit {result.returncode}"
        _loud_plugin_cache_failure(tail)
        return

    if not quiet:
        # Claude prints its own success line on stdout; relay it so the
        # version transition is visible in the install summary.
        last = (result.stdout or "").strip().splitlines()
        if last:
            console.print(f"  [green]{rich_escape(last[-1])}[/green]", markup=True)
        console.print(
            "  [dim]Restart Claude Code to load the refreshed plugin.[/dim]",
            markup=True,
        )


def _cmd_install(args: SimpleNamespace, mode: str) -> int:
    """Shared install / upgrade / check / uninstall driver. `mode` selects:
        install   → integration.plan(ctx)
        upgrade   → integration.plan(ctx) (same; the dispatcher reports no-op
                    when all actions are no-op kind=check)
        check     → integration.plan(ctx) (dry-run; never writes)
        uninstall → integration.uninstall_plan(ctx)
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
    integrations = _select_integrations(
        args.integration, interactive=(interactive and mode == "install")
    )
    if not integrations:
        console.print("  [yellow]No integrations selected.[/yellow]", markup=True)
        return 0
    ctx = _build_ctx(args, mode=mode)

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
            if mode != "check" and not args.force:
                if args.json:
                    _emit_json(
                        {
                            "ok": False,
                            "reason": "legacy_artifacts",
                            "artifacts": [a.__dict__ for a in legacy_artifacts],
                        }
                    )
                return 1

    # Build per-integration plan
    plans: list[tuple[str, list[Action]]] = []
    for integration_name in integrations:
        integration_module = REGISTRY[integration_name]
        if mode == "uninstall":
            actions = integration_module.uninstall_plan(ctx)
        else:
            actions = integration_module.plan(ctx)
        plans.append((integration_name, actions))

    # JSON output mode — emit the plan envelope and exit
    if args.json or mode == "check":
        envelope = {
            "mode": mode,
            "legacy_artifacts": [a.__dict__ for a in legacy_artifacts],
            "integrations": [
                {
                    "integration": name,
                    "actions": [a.to_dict() for a in actions],
                }
                for name, actions in plans
            ],
        }
        if args.json:
            _emit_json(envelope)
        else:
            for name, actions in plans:
                _print_integration_plan(name, actions)
        return 0

    # Interactive / --yes path
    overall_failures = 0
    for name, actions in plans:
        _print_integration_plan(name, actions)
        if not actions:
            continue
        choice = _prompt_integration(name, actions, args.yes)
        if choice == "n":
            console.print(f"\n  [yellow]skipped {name}[/yellow]", markup=True)
            continue
        if not args.quiet:
            console.print()  # blank line before per-action ✓ lines
        results, fail_count = _execute_actions(
            actions, yes=args.yes, quiet=args.quiet
        )
        overall_failures += fail_count
        _print_integration_summary(name, fail_count, mode)

    # Final handoff
    if mode == "install" and overall_failures == 0:
        # Refresh Claude's plugin manifest cache so hooks/skills/MCP land
        # without a manual `claude plugin update lore@lore`. Only when the
        # Claude integration was actually part of the run.
        if any(name == "claude" for name, _ in plans) and not args.json:
            _refresh_claude_plugin_cache(quiet=args.quiet)
        console.print(
            "\n[bold]Next:[/bold] run [cyan]lore init[/cyan] to scaffold "
            "your vault and finish onboarding.",
            markup=True,
        )
    return 0 if overall_failures == 0 else 1


def _make_args(
    *,
    integration: str | None,
    yes: bool,
    quiet: bool,
    json_out: bool,
    force: bool,
    lore_repo: str | None,
) -> SimpleNamespace:
    """Adapt typer kwargs into the namespace shape `_cmd_install` reads."""
    return SimpleNamespace(
        integration=integration,
        yes=yes,
        quiet=quiet,
        json=json_out,
        force=force,
        lore_repo=lore_repo,
    )


def _exit_with(rc: int) -> None:
    if rc:
        raise typer.Exit(code=rc)


# Common flag set shared across the install verbs. Typer doesn't share
# options between root + subcommands cleanly (Click constraint), so
# every command takes the same six options via these module-level
# defaults.

_INTEGRATION = typer.Option(
    None,
    "--integration",
    help="Integration to install for (claude|cursor|all). Default: all detected.",
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
_UPGRADE = typer.Option(
    False,
    "--upgrade",
    "-u",
    help=(
        "Upgrade the lore binary via install.sh before configuring "
        "integrations. install.sh chains back into `lore install` once "
        "the new binary is in place — single command, full roundtrip."
    ),
)
_CHECK = typer.Option(
    False, "--check", help="Only report whether an update is available; never upgrades."
)


def update_command(check: bool = _CHECK, quiet: bool = _QUIET) -> None:
    """Update Lore — Python package and Claude plugin — if a newer release
    has landed on `main`.

    Delegates to `_run_self_upgrade`, which runs `install.sh upgrade`: that
    script upgrades the pipx/uv/pip package and then chains into `lore
    install`, which refreshes the Claude plugin cache. So this one command
    covers both halves of the install.
    """
    local = _local_version()
    remote = _fetch_remote_version()
    if remote is None:
        console.print(
            "[red]Could not reach GitHub to check the latest lore version.[/red]\n"
            "  Force the upgrade path directly instead: "
            "[cyan]lore install --upgrade[/cyan]",
            markup=True,
        )
        raise typer.Exit(code=1)

    if not _remote_is_newer(remote, local):
        if not quiet:
            console.print(f"[green]lore {local} is up to date.[/green]", markup=True)
        return

    if not quiet:
        console.print(
            f"[bold]Update available:[/bold] {local} → {remote}", markup=True
        )
    if check:
        raise typer.Exit(code=1)

    raise typer.Exit(code=_run_self_upgrade(quiet=quiet))


def build_install_command(
    modes: str | tuple[str, ...], docstring: str
) -> Callable[..., None]:
    """Build a typer-compatible function that runs `_cmd_install` for one
    or more sequential modes.

    Single-mode entries (``"check"``, ``"upgrade"``, ``"uninstall"``)
    drive `_cmd_install` once; the chained form ``("uninstall",
    "install")`` powers the ``reinstall`` verb. Reused by the top-level
    ``lore uninstall`` alias in `lore_cli.__main__`.
    """
    seq = (modes,) if isinstance(modes, str) else modes

    def _cmd(
        integration: str = _INTEGRATION,
        yes: bool = _YES,
        quiet: bool = _QUIET,
        json_out: bool = _JSON,
        force: bool = _FORCE,
        lore_repo: str = _LORE_REPO,
    ) -> None:
        for i, mode in enumerate(seq):
            args = _make_args(
                integration=integration,
                yes=yes,
                quiet=quiet,
                json_out=json_out,
                force=force,
                lore_repo=lore_repo,
            )
            rc = _cmd_install(args, mode=mode)
            # Abort a chained run (reinstall) on the first failing step.
            if rc != 0 or i == len(seq) - 1:
                _exit_with(rc)
                return

    _cmd.__doc__ = docstring
    return _cmd


@dataclass(frozen=True)
class _Verb:
    """One row in the install-verb registry."""

    name: str
    modes: str | tuple[str, ...]
    docstring: str


_INSTALL_VERBS: tuple[_Verb, ...] = (
    _Verb("check", "check", "Plan-only — never writes."),
    _Verb(
        "upgrade",
        "upgrade",
        "Re-install — no-op if managed schema is current.",
    ),
    _Verb("uninstall", "uninstall", "Symmetric semantic remove."),
    _Verb(
        "reinstall",
        ("uninstall", "install"),
        (
            "Uninstall then install — useful after upgrading the Lore package.\n"
            "\n"
            "Equivalent to:\n"
            "\n"
            "    lore install uninstall && lore install\n"
            "\n"
            "The install pass automatically runs ``claude plugin update lore@lore``\n"
            "when the Claude integration is part of the run, so the plugin manifest\n"
            "cache stays in sync. The ``.claude-plugin/plugin.json`` version still\n"
            "has to be bumped in the source for Claude's update to do anything —\n"
            "see CHANGELOG.md."
        ),
    ),
)


app = typer.Typer(
    add_completion=False,
    help=__doc__,
    no_args_is_help=False,
    rich_markup_mode="rich",
)


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    integration: str = _INTEGRATION,
    yes: bool = _YES,
    quiet: bool = _QUIET,
    json_out: bool = _JSON,
    force: bool = _FORCE,
    lore_repo: str = _LORE_REPO,
    upgrade: bool = _UPGRADE,
) -> None:
    """Default action — install Lore for one or more integrations."""
    if ctx.invoked_subcommand is not None:
        return  # the subcommand handles its own work
    if upgrade:
        # Hand off to install.sh; it will exec the new `lore install`
        # after the binary is upgraded, so we never touch _cmd_install
        # in the old process.
        _exit_with(_run_self_upgrade(quiet=quiet))
        return
    args = _make_args(
        integration=integration,
        yes=yes,
        quiet=quiet,
        json_out=json_out,
        force=force,
        lore_repo=lore_repo,
    )
    _exit_with(_cmd_install(args, mode="install"))


for _verb in _INSTALL_VERBS:
    app.command(_verb.name)(build_install_command(_verb.modes, _verb.docstring))


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
