"""`lore setup` — the one command for first run, for a new wiki or scope, and for repair.

Four steps (PRD 0016 § Setup): wiki, agents, scope, check. A step writes only
when its target differs, so a second run on a healthy setup writes no file and
prints one check mark per step. Every prompt has a flag; without a terminal,
or with ``--yes``, every answer comes from flags and defaults.

Each run also migrates the old vault-local state (``attachments.json``,
``scopes.json``, ``$LORE_ROOT/.lore/config.yml``) into the person's
``~/.config/lore/config.yml`` and deletes the old files once it is written.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

import typer
import yaml
from lore_core.config import get_lore_root, list_wikis, resolve_lore_root, user_config_path
from lore_core.io import atomic_write_text
from rich.console import Console
from rich.markup import escape

from lore_cli import checks, integrations

console = Console(emoji=False, soft_wrap=True)

app = typer.Typer(
    add_completion=False,
    help="Set up or repair Lore: wiki, agents, scope, check.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)

#: (ok, message): True wrote or repaired something, False failed, None is advisory.
Event = tuple[bool | None, str]


# ---------------------------------------------------------------------------
# Person config
# ---------------------------------------------------------------------------


def _read_person() -> dict:
    path = user_config_path()
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        # Never rewrite a file we could not read: that would drop its content.
        raise typer.BadParameter(f"cannot parse {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise typer.BadParameter(f"{path} must hold a mapping")
    return raw


def _write_person(cfg: dict) -> None:
    # ponytail: yaml.safe_dump drops comments in a hand-edited file, like
    # `lore config set`; keeping them needs a round-trip YAML writer (ruamel),
    # which is not a dependency.
    atomic_write_text(user_config_path(), yaml.safe_dump(cfg, sort_keys=False))


def _merge_under(person: dict, old: dict) -> None:
    """Fill ``person`` with keys from ``old``; the person's own values win."""
    for key, value in old.items():
        if isinstance(value, dict) and isinstance(person.get(key), dict):
            _merge_under(person[key], value)
        else:
            person.setdefault(key, value)


# ---------------------------------------------------------------------------
# Migration (repair path)
# ---------------------------------------------------------------------------


def migrate(lore_root: Path) -> list[Event]:
    """Move ``attachments.json``, ``scopes.json`` and the old root config into
    the person's config, then delete them. One scope per old attachment."""
    old = lore_root / ".lore"
    cfg, att, scp = old / "config.yml", old / "attachments.json", old / "scopes.json"
    if not (cfg.exists() or att.exists() or scp.exists()):
        return []
    person = _read_person()
    events: list[Event] = []
    done: list[Path] = []

    if cfg.exists():
        try:
            raw = yaml.safe_load(cfg.read_text()) or {}
        except yaml.YAMLError as exc:
            raw = None
            events.append((False, f"{cfg} is not valid YAML, left in place: {exc}"))
        if isinstance(raw, dict):
            from lore_core.root_config import RETIRED_BLOCKS

            retired = sorted(k for k in raw if k in RETIRED_BLOCKS)
            if retired:
                names = ", ".join(repr(k) for k in retired)
                events.append((None, f"dropped retired block(s) {names} from {cfg}"))
            _merge_under(person, {k: v for k, v in raw.items() if k not in RETIRED_BLOCKS})
            events.append((True, f"moved the keys of {cfg} into {user_config_path()}"))
            done.append(cfg)
        elif raw is not None:
            events.append((False, f"{cfg} must hold a mapping, left in place"))

    if att.exists():
        try:
            data = json.loads(att.read_text())
            rows = data.get("attachments", [])
            declined = data.get("declined", [])
            if not isinstance(rows, list):
                raise ValueError("`attachments` must be a list")
        except (OSError, ValueError, AttributeError) as exc:
            rows = None
            events.append((False, f"{att} is unreadable, left in place: {exc}"))
        if rows is not None:
            scopes = person.setdefault("scopes", {})
            known = {s.get("path") for s in scopes.values() if isinstance(s, dict)}
            added = 0
            skipped = 0
            for row in rows:
                if not isinstance(row, dict):
                    skipped += 1
                    continue
                path, wiki = str(row.get("path") or ""), row.get("wiki")
                if path and not wiki:
                    events.append((None, f"not migrated: {path} (no wiki)"))
                if not path or not wiki or path in known:
                    continue
                # The old scope's leaf is the project slug that SessionStart's
                # orientation and `lint._repo_by_project_slug` match on.
                base = str(row.get("scope") or "").rsplit(":", 1)[-1] or Path(path).name or "scope"
                name, n = base, 2
                while name in scopes:
                    name, n = f"{base}-{n}", n + 1
                scopes[name] = {"path": path, "wiki": wiki}
                known.add(path)
                added += 1
            events.append((True, f"wrote {added} scope(s) from {att}"))
            if skipped:
                events.append((None, f"skipped {skipped} malformed row(s) in {att}"))
            # A declined repo stays unrouted; say so, since consent is gone.
            for row in declined if isinstance(declined, list) else ():
                if isinstance(row, dict) and row.get("path"):
                    events.append((None, f"not migrated: {row['path']} (declined)"))
            done.append(att)

    if scp.exists():
        events.append((True, f"dropped {scp}; scopes live in {user_config_path()} now"))
        done.append(scp)

    if done:
        _write_person(person)
    for path in done:
        path.unlink(missing_ok=True)
    return events


# ---------------------------------------------------------------------------
# Step 1 · Wiki
# ---------------------------------------------------------------------------


def _clone_name(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")


def step_wiki(
    *,
    vault: str | None,
    wiki_new: str | None,
    wiki_clone: str | None,
    wiki_link: str | None,
    interactive: bool,
    invent_vault: bool = True,
) -> tuple[str, list[Event]]:
    """Make sure a vault and at least one wiki exist; create, clone or link one.

    ``invent_vault=False`` (``lore setup wiki``, which install.sh's upgrade
    path runs) never creates a vault at the default path when nothing names
    one: ``LORE_ROOT`` may live only in Claude's environment.
    """
    if len([t for t in (wiki_new, wiki_clone, wiki_link) if t]) > 1:
        raise typer.BadParameter("Pass at most one of --wiki-new / --wiki-clone / --wiki-link.")
    events: list[Event] = []

    if vault:
        root = Path(vault).expanduser().resolve()
    elif resolve_lore_root() is not None:
        root = get_lore_root()
    elif interactive:
        root = Path(typer.prompt("Vault location", default=str(get_lore_root()))).expanduser()
        root = root.resolve()
    else:
        root = get_lore_root()
        if not invent_vault and not root.is_dir():
            return f"no vault at {root}", [
                (
                    None,
                    f"no vault configured and none at {root}; skipped. "
                    "Run `lore setup --vault <path>` to name one.",
                )
            ]

    person = _read_person()
    current = person.get("lore_root")
    if current is None or (vault and Path(current).expanduser().resolve() != root):
        person["lore_root"] = str(root)
        _write_person(person)
        events.append((True, f"lore_root → {root}"))
    if os.environ.get("LORE_ROOT") and Path(os.environ["LORE_ROOT"]).resolve() != root:
        os.environ["LORE_ROOT"] = str(root)  # the rest of this run must see the chosen vault

    for sub in ("wiki", "inbox"):
        if not (root / sub).is_dir():
            (root / sub).mkdir(parents=True)
            events.append((True, f"created {root / sub}"))
    if not (root / "CLAUDE.md").exists():
        from lore_core.templates import templates_dir

        (root / "CLAUDE.md").write_text((templates_dir() / "root-CLAUDE.md").read_text())
        events.append((True, f"wrote {root / 'CLAUDE.md'}"))

    existing = {p.name for p in list_wikis(root)}
    if not (wiki_new or wiki_clone or wiki_link) and not existing:
        if interactive:
            choice = typer.prompt(
                "First wiki: [n]ew personal, [c]lone a team wiki, [l]ink a folder", default="n"
            )
            if choice.lower().startswith("c"):
                wiki_clone = typer.prompt("Git remote URL")
            elif choice.lower().startswith("l"):
                wiki_link = typer.prompt("Folder")
            else:
                wiki_new = typer.prompt("Wiki name", default="personal")
        else:
            wiki_new = "personal"

    if wiki_clone and _clone_name(wiki_clone) not in existing:
        target = root / "wiki" / _clone_name(wiki_clone)
        rc = subprocess.run(["git", "clone", wiki_clone, str(target)], check=False).returncode
        events.append((rc == 0, f"cloned {wiki_clone} into {target}"))
    elif wiki_link:
        src = Path(wiki_link).expanduser().resolve()
        target = root / "wiki" / src.name
        if not (target.exists() or target.is_symlink()):
            target.symlink_to(src)
            events.append((True, f"linked wiki {src.name} → {src}"))
    elif wiki_new and wiki_new not in existing:
        from lore_cli.wiki_cmd import scaffold_wiki

        scaffold_wiki(wiki_new, mode="personal")
        events.append((True, f"created wiki {wiki_new}"))

    names = ", ".join(p.name for p in list_wikis(root)) or "none"
    return f"{root} · wikis: {names}", events


# ---------------------------------------------------------------------------
# Step 2 · Agents
# ---------------------------------------------------------------------------


def _present(name: str) -> bool:
    return integrations._integration_present(name)


def _install_integration(name: str) -> int:
    args = integrations._make_args(
        integration=name, yes=True, quiet=True, json_out=False, force=False, lore_repo=None
    )
    return integrations._cmd_install(args, mode="install")


def _refresh_plugin_cache() -> bool:
    """Refresh the marketplace catalog, then the plugins (as ``install.sh upgrade`` does)."""
    claude = shutil.which("claude")

    def run(*args: str) -> None:
        if claude:
            with contextlib.suppress(subprocess.TimeoutExpired, OSError):
                subprocess.run(
                    [claude, "plugin", *args], check=False, capture_output=True, timeout=60
                )

    run("marketplace", "update", "lore")
    ok = integrations._refresh_claude_plugin_cache(quiet=True)
    # lore-workflow versions on its own axis; without its own refresh its
    # skills silently stay on the old version (#311).
    try:
        index = json.loads(checks._plugin_index().read_text())
    except (OSError, ValueError):
        index = {}
    if index.get("plugins", {}).get("lore-workflow@lore"):
        run("update", "lore-workflow@lore")
    return ok


def step_agents(cwd: Path) -> tuple[str, list[Event]]:
    """Install or refresh the Claude Code plugin, and Cursor's wiring where present."""
    events: list[Event] = []
    present = [n for n in ("claude", "cursor") if _present(n)]

    if "claude" in present:
        try:
            cached = checks.claude_cache_version()
        except (OSError, ValueError) as exc:
            cached = ""
            events.append((None, f"Claude plugin index unreadable: {exc}"))
        pip = version("lore")
        if cached is None:
            ok = _install_integration("claude") == 0
            events.append((ok, "installed the Claude Code plugin lore@lore"))
        elif cached and cached != "unknown" and _behind(cached, pip):
            ok = _refresh_plugin_cache()
            events.append((ok, f"repaired the plugin cache: {cached} → {pip}"))

    if "cursor" in present:
        cursor_checks = (
            checks._check_cursor_plugin_dir,
            checks._check_cursor_mcp_command_resolves,
            checks._check_cursor_hooks_config,
        )
        stale = [msg for ok, msg in (fn(str(cwd)) for fn in cursor_checks) if not ok]
        if stale:
            ok = _install_integration("cursor") == 0
            events.append((ok, f"repaired the Cursor wiring ({'; '.join(stale)})"))

    return ", ".join(present) or "no Claude Code or Cursor found", events


def _behind(cached: str, pip: str) -> bool:
    try:
        return integrations._version_tuple(cached) < integrations._version_tuple(pip)
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Step 3 · Scope
# ---------------------------------------------------------------------------


def step_scope(
    cwd: Path,
    *,
    name: str | None,
    wiki: str | None,
    remote: list[str] | None,
    scaffold_workflow: bool,
    interactive: bool,
) -> tuple[str, list[Event]]:
    """Offer the repo's parent folder as a scope when no scope covers ``cwd``."""
    from lore_core.route import resolve_route

    route = resolve_route(cwd)
    events: list[Event] = []
    if scaffold_workflow and route.repo_root is not None:
        from lore_workflow.scaffold import scaffold

        if scaffold(route.repo_root):
            events.append((True, "scaffolded docs/prd, docs/adr and the AGENTS.md shim"))
    if route.scope:
        return f"{route.scope} covers {route.cwd}", events
    if route.repo_root is None:
        return "not in a git repo; no scope to offer", events

    folder = route.repo_root.parent
    home, vault = Path.home().resolve(), get_lore_root().resolve()
    if home.is_relative_to(folder) or folder.is_relative_to(vault):
        return f"not offering {folder}", [
            (
                None,
                f"{folder} is too broad for a scope (or inside the vault); "
                f"add one under `scopes:` in {user_config_path()}",
            )
        ]
    wikis = [p.name for p in list_wikis(get_lore_root())]
    name = name or folder.name
    wiki = wiki or route.wiki or (wikis[0] if wikis else "")
    remotes = list(remote or [])
    if interactive:
        if not typer.confirm(f"Add a scope for {folder} (every repo below it)?", default=True):
            return "declined", events
        name = typer.prompt("Scope name", default=name)
        wiki = typer.prompt(f"Wiki ({', '.join(wikis)})", default=wiki)
        extra = typer.prompt("Git remote pattern (optional)", default="", show_default=False)
        remotes += [extra] if extra else []
    if wiki not in wikis:
        known = ", ".join(wikis) or "none"
        return "unknown wiki", [
            (
                False,
                f"no wiki {wiki!r} under {get_lore_root() / 'wiki'}; known: {known}"
                " — run `lore setup wiki`",
            )
        ]

    person = _read_person()
    scopes = person.setdefault("scopes", {})
    taken = scopes.get(name)
    if isinstance(taken, dict) and taken.get("path") != str(folder):
        clash = f"scope {name!r} already names {taken.get('path')}; pass --name"
        return "name taken", [(False, clash)]
    entry: dict = {"path": str(folder), "wiki": wiki}
    if remotes:
        entry["remote"] = remotes
    scopes[name] = entry
    _write_person(person)
    events.append((True, f"added scope {name!r}: {folder} → wiki {wiki}"))
    return name, events


# ---------------------------------------------------------------------------
# Step 4 · Check
# ---------------------------------------------------------------------------


def step_check(cwd: Path) -> tuple[str, list[Event]]:
    """Run the install checks; a failing advisory check does not fail setup."""
    events: list[Event] = []
    advisory: list[str] = []
    for name, fn, fails_run in checks._CHECKS:
        ok, msg = fn(str(cwd))
        if not ok:
            events.append((False if fails_run else None, f"{name}: {msg}"))
            advisory += [] if fails_run else [name]
    total = len(checks._CHECKS)
    if not events:
        return f"{total} checks passed", events
    summary = f"{total - len(events)} of {total} checks passed"
    return summary + (f"; advisory failed: {', '.join(advisory)}" if advisory else ""), events


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

_MARK = {True: "[green]✓[/green]", False: "[red]✗[/red]", None: "[yellow]![/yellow]"}


def _report(step: str, summary: str, events: list[Event]) -> bool:
    """One ✓ line when nothing changed; else one line per change. False on a failure."""
    if all(ok is None for ok, _ in events):
        console.print(f"{_MARK[True]} {step:<8}{escape(summary)}")
    for ok, msg in events:
        console.print(f"{_MARK[ok]} {step:<8}{escape(msg)}")
    return all(ok is not False for ok, _ in events)


def _interactive(yes: bool) -> bool:
    return sys.stdin.isatty() and not yes


def _migrate_report() -> bool:
    events = migrate(get_lore_root())
    return _report("migrate", "", events) if events else True


def _finish(ok: bool) -> None:
    if not ok:
        raise typer.Exit(code=1)


_YES = typer.Option(False, "--yes", "-y", help="Take every answer from flags and defaults.")
_VAULT = typer.Option(None, "--vault", help="Vault location (default: configured, or ~/lore).")
_WIKI_NEW = typer.Option(None, "--wiki-new", help="Create a personal wiki with this name.")
_WIKI_CLONE = typer.Option(None, "--wiki-clone", help="Clone a team wiki from this git URL.")
_WIKI_LINK = typer.Option(None, "--wiki-link", help="Link an existing folder as a wiki.")
_NAME = typer.Option(None, "--name", help="Scope name (default: the folder's name).")
_WIKI = typer.Option(None, "--wiki", help="Wiki the scope routes to.")
_REMOTE = typer.Option(None, "--remote", help="Git remote pattern, e.g. github.com/org/*.")
_SCAFFOLD = typer.Option(
    False, "--scaffold-workflow", help="Also scaffold docs/prd, docs/adr and the AGENTS.md shim."
)


@app.callback(invoke_without_command=True)
def setup(
    ctx: typer.Context,
    yes: bool = _YES,
    vault: str = _VAULT,
    wiki_new: str = _WIKI_NEW,
    wiki_clone: str = _WIKI_CLONE,
    wiki_link: str = _WIKI_LINK,
    name: str = _NAME,
    wiki: str = _WIKI,
    remote: list[str] = _REMOTE,
    scaffold_workflow: bool = _SCAFFOLD,
) -> None:
    """Run the four steps: wiki, agents, scope, check. Safe to re-run."""
    if ctx.invoked_subcommand is not None:
        return
    interactive = _interactive(yes)
    cwd = Path.cwd()
    ok = _report(
        "wiki",
        *step_wiki(
            vault=vault,
            wiki_new=wiki_new,
            wiki_clone=wiki_clone,
            wiki_link=wiki_link,
            interactive=interactive,
        ),
    )
    ok &= _migrate_report()
    ok &= _report("agents", *step_agents(cwd))
    ok &= _report(
        "scope",
        *step_scope(
            cwd,
            name=name,
            wiki=wiki,
            remote=remote,
            scaffold_workflow=scaffold_workflow,
            interactive=interactive,
        ),
    )
    ok &= _report("check", *step_check(cwd))
    _finish(ok)


@app.command("wiki")
def setup_wiki(
    yes: bool = _YES,
    vault: str = _VAULT,
    wiki_new: str = _WIKI_NEW,
    wiki_clone: str = _WIKI_CLONE,
    wiki_link: str = _WIKI_LINK,
) -> None:
    """Create, clone or link a wiki (the Wiki step alone)."""
    ok = _report(
        "wiki",
        *step_wiki(
            vault=vault,
            wiki_new=wiki_new,
            wiki_clone=wiki_clone,
            wiki_link=wiki_link,
            interactive=_interactive(yes),
            invent_vault=bool(vault or wiki_new or wiki_clone or wiki_link),
        ),
    )
    _finish(ok & _migrate_report())


@app.command("scope")
def setup_scope(
    yes: bool = _YES,
    name: str = _NAME,
    wiki: str = _WIKI,
    remote: list[str] = _REMOTE,
    scaffold_workflow: bool = _SCAFFOLD,
) -> None:
    """Add a scope for the folder above this repo (the Scope step alone)."""
    ok = _migrate_report()
    ok &= _report(
        "scope",
        *step_scope(
            Path.cwd(),
            name=name,
            wiki=wiki,
            remote=remote,
            scaffold_workflow=scaffold_workflow,
            interactive=_interactive(yes),
        ),
    )
    _finish(ok)
