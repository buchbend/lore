"""Install checks — the Check step of `lore setup`.

Walks the most common breakage points; each check returns ``(ok, message)``
and writes nothing (the cache probe is reverted at once). Advisory checks
(``fails_run=False``) never fail setup.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

# A check takes the current cwd (most ignore it) and returns
# (ok: bool, message: str). Side-effect-free except for the
# cache-write probe which is reverted immediately.
Check = tuple[bool, str]


def _check_lore_root(cwd: str) -> Check:
    from lore_core.config import get_lore_root, lore_root_source

    root = get_lore_root()
    label = {
        "env": "$LORE_ROOT",
        "config": "config-file",
        "default": "unconfigured (fallback)",
    }[lore_root_source()]
    if not root.exists():
        return (
            False,
            f"LORE_ROOT={root} [{label}] — does not exist "
            "(set $LORE_ROOT, write ~/.config/lore/config.yml, or run `lore setup`)",
        )
    return True, f"LORE_ROOT={root} [{label}]"


def _check_wikis(cwd: str) -> Check:
    from lore_core.config import get_wiki_root

    wiki_root = get_wiki_root()
    if not wiki_root.exists():
        return False, f"{wiki_root} missing (run `lore setup` or `lore wiki new <name>`)"
    wikis = [p for p in sorted(wiki_root.iterdir()) if p.resolve().is_dir()]
    if not wikis:
        return False, f"no wikis under {wiki_root} (run `lore wiki new <name>`)"
    return True, f"{len(wikis)} wiki(s): {', '.join(w.name for w in wikis)}"


def _check_cache_writable(cwd: str) -> Check:
    cache_env = os.environ.get("LORE_CACHE")
    cache = Path(cache_env).expanduser() if cache_env else Path.home() / ".cache" / "lore"
    try:
        cache.mkdir(parents=True, exist_ok=True)
        probe = cache / ".doctor-probe"
        probe.write_text("ok")
        probe.unlink()
    except OSError as e:
        return False, f"cache {cache} not writable: {e}"
    return True, f"cache {cache} writable"


def _check_lore_version_drift(cwd: str) -> Check:
    """Compare the installed `lore` Python package version against the
    on-disk source tree (auto-detected by walking up from this file).

    See issue #28 — the Claude Code plugin and the Python CLI binary
    are on separate update channels (`claude plugin update` vs
    `pipx install`); drift between them causes SessionStart's status
    line to silently show the *binary's* old version even after a
    successful plugin update.
    """
    from lore_core.source_root import check_lore_version_match

    # Find the source repo root by walking up from this file looking
    # for a pyproject.toml. Returns None gracefully if running from a
    # PyPI install (no source tree adjacent).
    here = Path(__file__).resolve()
    repo: Path | None = None
    for ancestor in [here, *here.parents]:
        if (ancestor / "pyproject.toml").is_file() and (ancestor / "lib").is_dir():
            repo = ancestor
            break

    return check_lore_version_match(repo)


def _check_plugin_manifest_sync(cwd: str) -> Check:
    """Compare the source-tree plugin manifest version to the installed pip version.

    Catches the headline footgun documented in CHANGELOG: ``claude
    plugin update lore@lore`` is a separate channel from ``pipx
    install --upgrade lore``. If the user upgrades pip but doesn't
    refresh the plugin cache, new hooks (PostToolUse:ExitPlanMode in
    v0.14.0) silently don't fire.

    Limitation: lore can't directly interrogate Claude Code's plugin
    cache from outside the harness. The best we can do is compare
    the source-tree plugin.json version to the installed pip version
    (which test_version_sync.py already enforces in CI for
    consistency) AND surface an informational reminder.
    """
    import json as _json
    from importlib.metadata import PackageNotFoundError, version

    here = Path(__file__).resolve()
    repo: Path | None = None
    for ancestor in [here, *here.parents]:
        if (ancestor / "pyproject.toml").is_file() and (ancestor / "lib").is_dir():
            repo = ancestor
            break
    if repo is None:
        return True, (
            "skipped: no on-disk source tree to compare plugin.json — "
            "remember to run `/plugin update lore` after each pip upgrade"
        )

    manifest_path = repo / ".claude-plugin" / "plugin.json"
    if not manifest_path.exists():
        return True, (
            f"skipped: {manifest_path} not found — "
            "remember to run `/plugin update lore` after each pip upgrade"
        )

    try:
        manifest = _json.loads(manifest_path.read_text())
    except (OSError, ValueError) as e:
        return False, f"plugin.json unreadable: {e}"

    plugin_version = str(manifest.get("version") or "")
    if not plugin_version:
        return False, "plugin.json has no `version` field"

    try:
        installed = version("lore")
    except PackageNotFoundError:
        return False, "lore Python package not installed in this environment"

    if plugin_version != installed:
        return False, (
            f"plugin.json version {plugin_version} != installed pip {installed}; "
            "after `pipx install --upgrade lore` run `/plugin update lore` in Claude Code"
        )

    return True, (
        f"plugin.json {plugin_version} matches pip — but Claude Code's plugin "
        "cache is opaque from here. After every pip upgrade, run "
        "`/plugin update lore` in a Claude Code session to refresh hooks/skills/MCP."
    )


def _plugin_index() -> Path:
    return Path.home() / ".claude" / "plugins" / "installed_plugins.json"


def claude_cache_version() -> str | None:
    """Version of ``lore@lore`` in Claude Code's plugin cache, or None if absent.

    Prefers the user-scope entry, which SessionStart hooks bind to. Raises
    ``OSError``/``ValueError`` on an unreadable index.
    """
    index = _plugin_index()
    if not index.exists():
        return None
    entries = json.loads(index.read_text()).get("plugins", {}).get("lore@lore") or []
    entry = next((e for e in entries if e.get("scope") == "user"), entries[0] if entries else None)
    return None if entry is None else str(entry.get("version") or "").strip()


def _check_claude_plugin_cache_drift(cwd: str) -> Check:
    """Compare the Claude Code plugin cache version to the installed pip version.

    Complements ``_check_plugin_manifest_sync`` (which compares against the
    on-disk source tree): this check reads the actual plugin-cache state
    that ``claude plugin update lore@lore`` writes to. The mismatch this
    catches is the headline footgun:

      * user runs ``claude plugin update lore@lore`` → cache bumps
        (e.g. 0.10.0 → 0.13.1)
      * pipx-installed ``lore`` binary stays on 0.10.0
      * SessionStart's banner reads ``importlib.metadata.version("lore")``
        and silently reports the *binary* version, hiding the plugin
        update entirely

    Cache state lives in ``~/.claude/plugins/installed_plugins.json`` —
    plain JSON; opaque is too strong a word.
    """
    from importlib.metadata import PackageNotFoundError, version

    cache_index = _plugin_index()
    if not cache_index.exists():
        return True, (
            "skipped: no Claude plugin cache at "
            f"{cache_index} (Claude Code not installed?)"
        )
    try:
        cache_version = claude_cache_version()
    except (OSError, ValueError) as exc:
        return False, f"plugin cache index unreadable: {exc}"
    if cache_version is None:
        return True, (
            "skipped: lore@lore not present in Claude plugin cache "
            "(install via `/plugin` in Claude Code)"
        )

    if not cache_version or cache_version == "unknown":
        return True, (
            f"Claude plugin cache version is `{cache_version or 'missing'}` — "
            "skipping comparison"
        )

    try:
        installed = version("lore")
    except PackageNotFoundError:
        return False, "lore Python package not installed in this environment"

    if cache_version == installed:
        return True, f"Claude plugin cache {cache_version} matches pip"

    from lore_cli.integrations import _version_tuple

    try:
        plugin_behind = _version_tuple(cache_version) < _version_tuple(installed)
    except ValueError:
        plugin_behind = False
    if plugin_behind:
        return False, (
            f"Claude plugin cache is {cache_version} but pip-installed lore is "
            f"{installed}. The plugin is behind. Run: claude plugin marketplace "
            "update lore && claude plugin update lore@lore, then restart Claude Code."
        )

    return False, (
        f"Claude plugin cache is {cache_version} but pip-installed lore is "
        f"{installed}. SessionStart's banner will report `{installed}` "
        "until you upgrade the binary. Run: pipx upgrade lore "
        "(or `pipx install --force --editable <path-to-lore-repo>`), then "
        "restart Claude Code."
    )


def _check_recent_crashes(cwd: str) -> Check:
    """Report hook crashes recorded in the last 7 days.

    Crash logs are written by `lore_cli._crash_log.write_crash` whenever
    a hook escapes ``_shield_hook`` or the top-level ``main()`` backstop
    catches an unexpected exception. Surfacing them here gives the user
    a single place to discover silent failures Claude Code suppressed
    behind the friendly banner.

    Advisory only — recent crashes don't fail the install (the user
    might already know about them and be working on a fix).
    """
    from lore_cli._crash_log import recent_crashes

    crashes = recent_crashes(within_days=7)
    if not crashes:
        return True, "no hook crashes in last 7 days"
    latest = crashes[0]
    return False, (
        f"{len(crashes)} hook crash(es) in last 7 days; latest: {latest}"
    )


def _check_spine_writable(cwd: str) -> Check:
    """Surface a spine write-failure degrade marker, if present.

    The event spine (`lore_core.spine.SpineWriter`) is best-effort and
    never raises on the hot path; a failed write touches
    ``.lore/spine-failed.marker`` instead. Doctor reads that marker so a
    telemetry blackout (full disk, bad permissions) is not itself silent.

    Advisory only — the marker records a *past* failure; the writer
    retries on the next event.
    """
    from datetime import UTC, datetime

    from lore_core.config import get_lore_root

    marker = get_lore_root() / ".lore" / "spine-failed.marker"
    if not marker.exists():
        return True, "event spine writes OK"
    try:
        mtime = datetime.fromtimestamp(marker.stat().st_mtime, tz=UTC)
        when = mtime.isoformat().replace("+00:00", "Z")
    except OSError:
        when = "unknown time"
    return False, (
        f"spine write failed (last at {when}) \u2014 check disk / permissions; "
        f"clear {marker} once resolved"
    )


def _check_hook_runnable(cwd: str) -> Check:
    """Run `lore hook session-start --plain --probe` and confirm it produces output.

    `--probe` suppresses side-effects (curator spawns, stamp/lock writes, ledger
    mutations) so the diagnostic doesn't mutate the thing it's diagnosing.
    """
    cmd = [sys.executable, "-m", "lore_cli", "hook", "session-start", "--plain", "--probe",
           "--cwd", cwd]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"hook exec failed: {e}"
    if result.returncode != 0:
        return False, f"hook exited {result.returncode}: {result.stderr.strip()[:200]}"
    if not result.stdout.strip():
        return False, "hook produced empty output (no wiki resolved?)"
    first_line = result.stdout.strip().splitlines()[0][:80]
    return True, f"hook → `{first_line}`"


def _check_mcp_imports(cwd: str) -> Check:
    try:
        import lore_mcp.server  # noqa: F401

        # Confirm tool schema generation works
        schema = lore_mcp.server._tool_schema()  # noqa: SLF001
    except Exception as e:
        return False, f"MCP server import/schema failed: {e}"

    # A schema that builds does not mean the server can start. `start_server`
    # registers its handlers through decorators that exist on the mcp 1.x
    # Server only; 2.0 replaced them with add_request_handler. Without this
    # check the panel reports "ready" for an install where every tool is dead.
    try:
        from mcp.server import Server
    except Exception as e:
        return False, f"mcp.server import failed: {e}"
    missing = [n for n in ("list_tools", "call_tool") if not hasattr(Server, n)]
    if missing:
        return False, (
            f"incompatible mcp release: Server has no {', '.join(missing)} — "
            f"the server raises AttributeError at startup. Needs mcp<2."
        )

    return True, f"MCP server ready ({len(schema)} tools)"


def _check_search_backend(cwd: str) -> Check:
    try:
        from lore_search.fts import FtsBackend

        backend = FtsBackend()
        stats = backend.stats()
    except Exception as e:
        return False, f"FTS backend failed: {e}"
    return True, f"FTS index: {stats.get('total_notes', '?')} notes"


def _check_vale(cwd: str) -> Check:
    """Vale is PATH-detected, not bundled — absence degrades the
    writing-rules lint to instruction-only and never blocks (ADR 0006)."""
    path = shutil.which("vale")
    if path is None:
        return False, "vale not on PATH — writing-rules lint is instruction-only until installed"
    return True, f"vale found at {path}"


# ---------------------------------------------------------------------------
# Cursor integration checks (advisory — `fails_run=False`)
#
# All three checks are gated on ``~/.cursor/`` existing. We use the
# directory marker — not ``shutil.which("cursor")`` — because most
# Linux/macOS users install Cursor as a GUI .deb / .AppImage / .dmg
# without a CLI on PATH, and we'd silently miss them otherwise.
# ---------------------------------------------------------------------------


def _cursor_installed() -> bool:
    """True when Cursor is installed on this host (any flavor)."""
    return (Path.home() / ".cursor").is_dir()


def _check_cursor_plugin_dir(cwd: str) -> Check:
    """Plugin dir exists with sentinel + manifest version matches lore."""
    if not _cursor_installed():
        return True, "skipped: ~/.cursor not present"

    from importlib.metadata import PackageNotFoundError, version

    from lore_core.install._helpers import cursor_plugin_dir
    from lore_core.managed_files import PLUGIN_SENTINEL

    plugin_dir = cursor_plugin_dir()
    if not plugin_dir.exists():
        return False, (
            f"{plugin_dir} not present — run `lore setup` "
            "to materialize the plugin"
        )
    if not (plugin_dir / PLUGIN_SENTINEL).exists():
        return False, (
            f"{plugin_dir} missing {PLUGIN_SENTINEL} sentinel — directory may "
            "predate plugin packaging; reinstall with --force"
        )
    manifest_path = plugin_dir / ".cursor-plugin" / "plugin.json"
    if not manifest_path.exists():
        return False, f"{manifest_path} not present — reinstall to refresh"
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError) as exc:
        return False, f"plugin manifest unreadable: {exc}"
    plugin_version = str(manifest.get("version") or "").strip()
    if not plugin_version:
        return False, "plugin manifest has no `version` field"
    try:
        installed = version("lore")
    except PackageNotFoundError:
        return False, "lore Python package not installed in this environment"
    if plugin_version != installed:
        return False, (
            f"plugin manifest {plugin_version} != installed pip {installed} — "
            "run `lore setup` after each pip upgrade"
        )
    return True, f"plugin {plugin_version} matches pip"


def _check_cursor_mcp_command_resolves(cwd: str) -> Check:
    """Plugin-local mcp.json `command` resolves to an existing executable.

    Catches the sticky-abs-path footgun: at install time the lore CLI
    is at one absolute path; after a pipx reinstall (different venv
    UUID) or a binary move the path goes stale and Cursor silently
    fails to spawn the MCP server with no Cursor-side error message.
    """
    if not _cursor_installed():
        return True, "skipped: ~/.cursor not present"

    from lore_core.install._helpers import cursor_plugin_dir

    plugin_dir = cursor_plugin_dir()
    mcp_path = plugin_dir / "mcp.json"
    if not mcp_path.exists():
        return False, f"{mcp_path} not present — reinstall to materialize"
    try:
        data = json.loads(mcp_path.read_text())
    except (OSError, ValueError) as exc:
        return False, f"plugin-local mcp.json unreadable: {exc}"
    entry = (data.get("mcpServers") or {}).get("lore") or {}
    cmd = entry.get("command")
    if not cmd:
        return False, "mcpServers.lore has no `command` field"
    cmd_path = Path(cmd)
    if not cmd_path.is_absolute():
        return False, (
            f"command is relative ({cmd!r}) — Cursor's GUI subprocess "
            "PATH does not include ~/.local/bin; reinstall to fix"
        )
    if not cmd_path.exists():
        return False, (
            f"command points to {cmd} which no longer exists — "
            "after `pipx upgrade lore`, run `lore setup`"
        )
    if not os.access(cmd_path, os.X_OK):
        return False, f"command {cmd} is not executable"
    return True, f"mcp command {cmd} resolves"


def _check_cursor_hooks_config(cwd: str) -> Check:
    """Plugin-local hooks.json parses with the expected event coverage."""
    if not _cursor_installed():
        return True, "skipped: ~/.cursor not present"

    from lore_core.install._helpers import cursor_plugin_dir

    plugin_dir = cursor_plugin_dir()
    hooks_path = plugin_dir / "hooks.json"
    if not hooks_path.exists():
        return False, f"{hooks_path} not present — reinstall to materialize"
    try:
        data = json.loads(hooks_path.read_text())
    except (OSError, ValueError) as exc:
        return False, f"hooks.json unreadable: {exc}"
    if data.get("version") != 1:
        return False, f"hooks.json version is {data.get('version')!r} (expected 1)"
    hooks = data.get("hooks") or {}
    if not hooks:
        return False, "hooks.json has empty hooks block"
    return True, f"{len(hooks)} event(s) wired: {', '.join(sorted(hooks))}"


def person_config_errors() -> list[str]:
    """Validate the person's config as `lore config edit` does (secrets rule included)."""
    import yaml
    from lore_core.config import user_config_path
    from lore_core.config_schema import validate_raw
    from lore_core.root_config import RootConfig

    path = user_config_path()
    if not path.exists():
        return []
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError) as e:
        return [f"cannot read {path}: {e}"]
    return validate_raw(RootConfig, raw, source=path)


def old_settings_files() -> list[Path]:
    """Settings files `lore setup` migrates and deletes (PRD 0016)."""
    from lore_core.config import get_lore_root

    old = get_lore_root() / ".lore"
    names = ("attachments.json", "scopes.json", "config.yml")
    return [old / n for n in names if (old / n).exists()]


OLD_SETTINGS_HINT = "old settings files found; run `lore setup` to migrate them"


def _check_person_config(cwd: str) -> Check:
    from lore_core.config import user_config_path

    errors = person_config_errors()
    if errors:
        return False, f"{user_config_path()}: " + "; ".join(errors)
    return True, f"{user_config_path()} is valid"


def _check_old_settings(cwd: str) -> Check:
    files = old_settings_files()
    if files:
        return False, f"{OLD_SETTINGS_HINT}: {', '.join(map(str, files))}"
    return True, "no old settings files"


# (name, check_fn, fails_run). `fails_run=False` means the check is
# informational — its `ok=False` is rendered as ✗ but does not set the
# overall non-zero exit.
_CHECKS: list[tuple[str, Callable[[str], Check], bool]] = [
    ("LORE_ROOT", _check_lore_root, True),
    # The secrets rule (ADR 0016) at load time, not only in `lore config edit`.
    ("person config", _check_person_config, True),
    # Advisory: `pipx upgrade` skips install.sh, so nothing else migrates.
    ("old settings", _check_old_settings, False),
    ("wikis", _check_wikis, True),
    ("cache", _check_cache_writable, True),
    ("MCP server", _check_mcp_imports, True),
    ("FTS backend", _check_search_backend, True),
    ("SessionStart hook", _check_hook_runnable, True),
    # Advisory: surfaces a past event-spine write failure via its degrade
    # marker so a telemetry blackout is not itself silent.
    ("event spine", _check_spine_writable, False),
    # Advisory: surfaces silent failures Claude Code hides behind the
    # friendly hook banner. Doesn't fail the install — the user may
    # already be triaging.
    ("recent crashes", _check_recent_crashes, False),
    # Advisory: version drift is informational. The CLI still functions
    # at the older version; the user just sees a stale SessionStart line.
    ("CLI version", _check_lore_version_drift, False),
    # Advisory: plugin-cache drift only matters once the user has
    # upgraded pip; the message itself is the value (it tells them
    # to run /plugin update lore).
    ("plugin manifest", _check_plugin_manifest_sync, False),
    # Failing (was advisory): catches the inverse — user ran `claude
    # plugin update` but didn't refresh the pipx binary. Banner silently
    # reports the old version; this names the fix command. The drift is
    # common enough in practice that it should block a green `doctor` run.
    ("plugin cache", _check_claude_plugin_cache_drift, True),
    # Cursor integration checks. All advisory: a Claude-only user has
    # no ~/.cursor dir and these all skip cleanly.
    ("cursor plugin", _check_cursor_plugin_dir, False),
    ("cursor mcp", _check_cursor_mcp_command_resolves, False),
    ("cursor hooks", _check_cursor_hooks_config, False),
    ("vale", _check_vale, False),
]
