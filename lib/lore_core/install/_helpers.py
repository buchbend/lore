"""Install-specific helpers for the per-integration install modules.

What lives here is what only an *installer* needs:

  Path resolution         per-platform config paths for each integration
  Self-install            installer cascade + binary-presence checks
  Cursor packaging        translate the Claude plugin manifest into
                          Cursor's manifest + hooks schema
  Legacy detection        find install.sh-era state still on disk
  Action execution        switch on Action.kind to preview / apply / undo

The general-purpose file primitives these executors are built on (atomic
JSON merge, managed markdown blocks, sentinel-gated trees) live in
`lore_core.managed_files`; source-tree resolution lives in
`lore_core.source_root`. Both are used outside install and are imported,
not re-exported, here.

No imports from `lore_cli` (the CLI dispatcher imports from us, not the
other way).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from lore_core import managed_files
from lore_core.install.base import (
    KIND_CHECK,
    KIND_DELETE,
    KIND_MERGE,
    KIND_NEW,
    KIND_REPLACE,
    KIND_RUN,
    Action,
    ApplyResult,
    LegacyArtifact,
)
from lore_core.io import atomic_write_text
from lore_core.source_root import check_lore_version_match

# ---------------------------------------------------------------------------
# Per-platform path resolution
# ---------------------------------------------------------------------------


def claude_config_dir() -> Path:
    """Resolve Claude Code's user config dir.

    Same on Linux + macOS today (`~/.claude`). Windows refuses with a
    NotImplementedError so the caller can surface a clean message.
    """
    if sys.platform.startswith("win"):
        raise NotImplementedError(
            "Windows is not supported in v1. Tracked: see issue list."
        )
    return Path.home() / ".claude"


def cursor_config_dir() -> Path:
    """Resolve Cursor's MCP config dir per platform.

    macOS  → ~/Library/Application Support/Cursor/User/
    Linux  → ${XDG_CONFIG_HOME:-~/.config}/Cursor/User/ if exists,
             else ~/.cursor/  (legacy; older Cursor versions still
             honour it)
    Windows → NotImplementedError
    """
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Cursor" / "User"
    if sys.platform.startswith("win"):
        raise NotImplementedError(
            "Windows is not supported in v1. Tracked: see issue list."
        )
    # Linux + other POSIX
    xdg = os.environ.get("XDG_CONFIG_HOME")
    xdg_path = (
        Path(xdg).expanduser() / "Cursor" / "User"
        if xdg
        else Path.home() / ".config" / "Cursor" / "User"
    )
    legacy = Path.home() / ".cursor"
    if xdg_path.exists():
        return xdg_path
    if legacy.exists():
        return legacy
    # Neither exists — prefer XDG location for future writes
    return xdg_path


def cursor_rules_dir() -> Path:
    """Cursor's rules directory — sits alongside mcp.json in the same root."""
    config = cursor_config_dir()
    if config.name == "User":
        # Modern XDG / macOS layout: rules under User/
        return config / "rules"
    # Legacy ~/.cursor/ layout: rules at top level
    return config / "rules"


def cursor_plugin_dir() -> Path:
    """Where Cursor 2.5+ scans for locally-installed plugins.

    Always under ``~/.cursor/`` (NOT the legacy ``~/.config/Cursor/User/``
    XDG path) because the local-plugin discovery surface is documented
    only at the ``~/.cursor/plugins/local/`` location.
    """
    return Path.home() / ".cursor" / "plugins" / "local" / "lore"


# ---------------------------------------------------------------------------
# Self-install + binary-presence checks
# ---------------------------------------------------------------------------

INSTALLERS = ("pipx", "uv", "pip")  # cascade order

# `lore` on PyPI is squatted by an unrelated package (lore 0.8.6 — broken
# on Python 3.13 due to pkg_resources). Until we publish under a different
# name (tracked in an issue), the canonical non-editable install path is
# the GitHub repo.
LORE_GIT_URL = "git+https://github.com/buchbend/lore.git"

# Per-installer argv prefix. The editable flag and the source argument are
# appended by `install_self_via`; all three installers spell them the same.
_INSTALL_ARGV = {
    "pipx": ["pipx", "install", "--force"],
    "uv": ["uv", "tool", "install", "--force"],
    "pip": ["pip", "install", "--user", "--force-reinstall"],
}


def install_self_via(target: Path | None = None) -> tuple[str, list[str]]:
    """Pick the first available installer and return its argv.

    `target` is the editable source path for dev installs (or None to
    install from the GitHub repo via git+ URL — PyPI publish is blocked
    on the `lore` name being squatted).

    Returns `(installer_name, argv)`. Caller invokes via subprocess.
    Raises RuntimeError if none are available.
    """
    src = str(target) if target else LORE_GIT_URL
    for installer in INSTALLERS:
        if shutil.which(installer):
            argv = list(_INSTALL_ARGV[installer])
            if target:
                argv.append("--editable")
            argv.append(src)
            return installer, argv
    raise RuntimeError(
        "No Python installer found (tried pipx, uv, pip). "
        "Install one and re-run."
    )


def check_lore_on_path() -> tuple[bool, str]:
    """Return (ok, message). Failure message includes the right next step."""
    if shutil.which("lore"):
        return True, "lore CLI on PATH"
    return False, (
        "lore not on PATH. Run: pipx ensurepath; then reopen your shell "
        "and re-run lore install. (If pipx isn't installed, the "
        "claude integration self-install bootstrap will offer to add it.)"
    )


# ---------------------------------------------------------------------------
# Canonical Lore MCP server entry — one source of truth for both integrations.
# ---------------------------------------------------------------------------


def lore_mcp_entry(schema_version: str) -> dict[str, Any]:
    """The mcpServers.lore block we write into shared MCP config files.

    Resolves `lore` to an absolute path via `shutil.which` because GUI
    MCP clients (notably Cursor) inherit a minimal PATH from systemd /
    desktop launchers and won't find pipx-installed binaries via the
    bare name. Falls back to `"lore"` when nothing is on PATH so unit
    tests in clean envs still pass.

    Includes `_lore_schema_version` so future migrations know whether
    the entry is Lore-managed. Underscore prefix discourages user
    edits.
    """
    return {
        "command": shutil.which("lore") or "lore",
        "args": ["mcp"],
        managed_files.SCHEMA_VERSION_KEY: schema_version,
    }


# ---------------------------------------------------------------------------
# Cursor plugin packaging — manifest + hooks generators
# ---------------------------------------------------------------------------

# Maps Claude Code hook event names to Cursor's hook event names.
# Sourced from cursor.com/docs/hooks (Cursor 1.7+, Sept 2025).
_CLAUDE_TO_CURSOR_EVENT = {
    "SessionStart": "sessionStart",
    "SessionEnd": "sessionEnd",
    "PreCompact": "preCompact",
    "Stop": "stop",
    "UserPromptSubmit": "beforeSubmitPrompt",
    "PostToolUse": "postToolUse",
    "PreToolUse": "preToolUse",
}


def _resolve_lore_in_command(cmd: str) -> str:
    """Replace a leading bare ``lore`` token with its absolute path.

    Mirrors the lore_mcp_entry resolution: GUI MCP / hook clients inherit
    a minimal PATH and will fail to spawn `lore` by bare name. Only
    rewrites when the command starts with `lore ` (or is exactly `lore`)
    to avoid touching user-customized hook commands.
    """
    abs_lore = shutil.which("lore")
    if not abs_lore:
        return cmd
    if cmd == "lore":
        return abs_lore
    if cmd.startswith("lore "):
        return abs_lore + cmd[len("lore"):]
    return cmd


def generate_cursor_plugin_manifest(
    claude_manifest: dict[str, Any],
) -> dict[str, Any]:
    """Generate a ``.cursor-plugin/plugin.json`` from the Claude manifest.

    Cursor's manifest schema is a strict subset: ``name`` (required),
    plus optional ``description``, ``version``, ``author``. Hooks and
    MCP servers live in sibling files (``hooks.json`` / ``mcp.json``)
    inside the plugin dir, not in the manifest itself.
    """
    out: dict[str, Any] = {"name": claude_manifest.get("name", "lore")}
    for key in ("description", "version", "author", "homepage", "license"):
        if key in claude_manifest:
            out[key] = claude_manifest[key]
    return out


def _flatten_hook_group(group: Any) -> list[dict[str, Any]]:
    """Flatten one Claude hook group into Cursor's per-entry hook list.

    Claude nests hooks under a group that carries the matcher; Cursor has
    no group level, so the group matcher is propagated onto each entry.
    """
    if not isinstance(group, dict):
        return []
    group_matcher = group.get("matcher")
    flat: list[dict[str, Any]] = []
    for hook in group.get("hooks") or []:
        if not isinstance(hook, dict):
            continue
        cmd = hook.get("command")
        if not cmd:
            continue
        entry: dict[str, Any] = {
            "type": hook.get("type", "command"),
            "command": _resolve_lore_in_command(cmd),
        }
        if group_matcher:
            entry["matcher"] = group_matcher
        flat.append(entry)
    return flat


def generate_cursor_hooks_json(
    claude_manifest: dict[str, Any],
) -> dict[str, Any]:
    """Generate a Cursor ``hooks.json`` from the Claude manifest's hooks block.

    Hook commands of the form ``lore <subcmd>`` are rewritten with the
    absolute ``lore`` path (same reason as MCP entries — GUI subprocess
    PATH is minimal).

    Timeout is intentionally omitted: Cursor's default is generous
    enough for cold-cache SessionStart cascades, and matching Claude's
    behavior (no explicit timeout) avoids spurious failures.
    """
    claude_hooks = claude_manifest.get("hooks") or {}
    out_hooks: dict[str, list[dict[str, Any]]] = {}
    for claude_event, groups in claude_hooks.items():
        cursor_event = _CLAUDE_TO_CURSOR_EVENT.get(claude_event)
        if not cursor_event:
            continue
        flat: list[dict[str, Any]] = []
        for group in groups or []:
            flat.extend(_flatten_hook_group(group))
        if flat:
            out_hooks[cursor_event] = flat
    return {"version": 1, "hooks": out_hooks}


# ---------------------------------------------------------------------------
# Legacy artifact detection
# ---------------------------------------------------------------------------

_LEGACY_HOOK_COMMAND_PREFIX = "lore hook"
_LEGACY_PERMISSION_RULES = {
    "Bash(lore:*)",
    "Bash(lore *)",
}


def _legacy_symlinks(
    directory: Path,
    *,
    name_prefix: str,
    kind: str,
    lore_repo: Path | None,
) -> list[LegacyArtifact]:
    """Lore-owned symlinks install.sh dropped into a ~/.claude subdirectory.

    `lore_repo` narrows the match to links pointing into that repo; with
    no repo given, any target under a `/lore/` path counts.
    """
    if not directory.is_dir():
        return []
    found: list[LegacyArtifact] = []
    for entry in sorted(directory.iterdir()):
        if not entry.name.startswith(name_prefix) or not entry.is_symlink():
            continue
        target = os.readlink(entry)
        if lore_repo is not None and str(lore_repo) not in target:
            continue
        if "/lore/" not in target and lore_repo is None:
            continue
        found.append(LegacyArtifact(kind=kind, path=str(entry), detail=target))
    return found


def _legacy_settings_artifacts(settings_path: Path) -> list[LegacyArtifact]:
    """Hook entries, permission rules and env vars install.sh wrote into settings.json."""
    if not settings_path.exists():
        return []
    try:
        cfg = json.loads(settings_path.read_text())
    except json.JSONDecodeError:
        cfg = {}

    found: list[LegacyArtifact] = []
    for event, group_list in (cfg.get("hooks") or {}).items():
        for grp in group_list:
            for h in grp.get("hooks") or []:
                cmd = h.get("command", "")
                if isinstance(cmd, str) and cmd.startswith(
                    _LEGACY_HOOK_COMMAND_PREFIX
                ):
                    found.append(
                        LegacyArtifact(
                            kind="hook_entry",
                            path=str(settings_path),
                            detail=f"{event}: {cmd}",
                        )
                    )

    allow = (cfg.get("permissions") or {}).get("allow") or []
    for rule in allow:
        if rule in _LEGACY_PERMISSION_RULES:
            found.append(
                LegacyArtifact(
                    kind="permission_rule",
                    path=str(settings_path),
                    detail=rule,
                )
            )

    if "LORE_ROOT" in (cfg.get("env") or {}):
        found.append(
            LegacyArtifact(
                kind="env_entry",
                path=str(settings_path),
                detail=f"LORE_ROOT={cfg['env']['LORE_ROOT']}",
            )
        )
    return found


def detect_install_sh_artifacts(
    lore_repo: Path | None = None,
) -> list[LegacyArtifact]:
    """Scan ~/.claude for install.sh-era state.

    Returns artifacts in a stable order: skill symlinks, then agent
    symlinks, then settings.json mutations.
    """
    claude = Path.home() / ".claude"
    return [
        *_legacy_symlinks(
            claude / "skills",
            name_prefix="lore:",
            kind="skill_symlink",
            lore_repo=lore_repo,
        ),
        *_legacy_symlinks(
            claude / "agents",
            name_prefix="lore-",
            kind="agent_symlink",
            lore_repo=lore_repo,
        ),
        *_legacy_settings_artifacts(claude / "settings.json"),
    ]


# ---------------------------------------------------------------------------
# Action executors — preview / apply / undo, dispatched on Action.kind
# ---------------------------------------------------------------------------


def preview_action(action: Action) -> str:
    """Return a multi-line diff/preview without side effects."""
    if action.kind == KIND_NEW:
        path = action.payload["path"]
        copy_from = action.payload.get("copy_from")
        if copy_from:
            return f"+++ {path}/ (copy tree from {copy_from})"
        content = action.payload["content"]
        return f"+++ {path} (new)\n" + "\n".join(
            f"+ {line}" for line in content.splitlines()
        )
    if action.kind == KIND_MERGE:
        path = action.payload["path"]
        kp = action.payload.get("key_path") or []
        return (
            f"--- {path}\n+++ {path}\n"
            f"   add key: {' / '.join(kp)}\n"
            f"   schema_version: {action.payload.get('schema_version', '?')}"
        )
    if action.kind == KIND_REPLACE:
        path = action.payload["path"]
        kp = action.payload.get("key_path") or []
        reason = action.payload.get("reason", "(no reason)")
        return (
            f"--- {path}\n+++ {path}\n"
            f"   replace key: {' / '.join(kp)}\n"
            f"   reason: {reason}"
        )
    if action.kind == KIND_RUN:
        return f"$ {' '.join(action.payload.get('argv') or [])}"
    if action.kind == KIND_CHECK:
        return f"check: {action.payload.get('check', '?')}"
    if action.kind == KIND_DELETE:
        path = action.payload["path"]
        kp = action.payload.get("key_path")
        if kp:
            return f"--- {path}\n   delete key: {' / '.join(kp)}"
        if action.payload.get("recursive"):
            return f"--- {path}/ (remove tree, sentinel-gated)"
        return f"--- {path} (remove)"
    raise ValueError(f"unknown action kind: {action.kind}")


def _real_path(path: Path) -> Path:
    """Resolve symlinks for an existing path, else leave it alone.

    Writes follow the symlink so dotfile managers (chezmoi, Stow) keep
    their links instead of having them replaced by a regular file.
    """
    return Path(os.path.realpath(path)) if path.exists() else path


def _set_key(key_path: list, value: Any) -> callable:  # type: ignore[type-arg]
    """Mutator that writes `value` at the nested `key_path`, creating parents."""

    def _mutator(data: dict) -> dict:
        cur = data
        for key in key_path[:-1]:
            cur = cur.setdefault(key, {})
        cur[key_path[-1]] = value
        return data

    return _mutator


def _del_key(key_path: list) -> callable:  # type: ignore[type-arg]
    """Mutator that removes the nested `key_path`; a no-op when it is absent."""

    def _mutator(data: dict) -> dict:
        cur = data
        for key in key_path[:-1]:
            if not isinstance(cur, dict) or key not in cur:
                return data
            cur = cur[key]
        if isinstance(cur, dict) and key_path[-1] in cur:
            del cur[key_path[-1]]
        return data

    return _mutator


def _has_key(key_path: list) -> callable:  # type: ignore[type-arg]
    """Validator asserting the nested `key_path` survived the write."""

    def _validator(data: dict) -> bool:
        cur = data
        for key in key_path:
            if not isinstance(cur, dict) or key not in cur:
                return False
            cur = cur[key]
        return True

    return _validator


def _exec_new(action: Action, schema_version: str) -> ApplyResult:
    path = Path(action.payload["path"]).expanduser()
    copy_from = action.payload.get("copy_from")
    if copy_from:
        managed_files.copy_dir_atomic(Path(copy_from).expanduser(), path)
        return ApplyResult(ok=True)
    real = _real_path(path)
    real.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(real, action.payload["content"])
    return ApplyResult(ok=True)


def _exec_merge(action: Action, schema_version: str) -> ApplyResult:
    path = Path(action.payload["path"]).expanduser()
    key_path = list(action.payload["key_path"])
    managed_files.json_merge_atomic(
        path,
        _set_key(key_path, action.payload["value"]),
        validate=_has_key(key_path),
    )
    return ApplyResult(ok=True)


def _exec_replace(action: Action, schema_version: str) -> ApplyResult:
    # Replace behaves exactly like merge on disk; the two kinds differ only
    # in how the dispatcher prompts the user before getting here.
    return execute_action(
        Action(
            kind=KIND_MERGE,
            description=action.description,
            target=action.target,
            summary=action.summary,
            payload={
                "path": action.payload["path"],
                "key_path": action.payload["key_path"],
                "value": action.payload["new_value"],
                "schema_version": schema_version,
            },
        ),
        schema_version=schema_version,
    )


def _exec_run(action: Action, schema_version: str) -> ApplyResult:
    argv = action.payload["argv"]
    fallback = action.payload.get("fallback_message")
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=action.payload.get("timeout", 60),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        msg = f"{e} — {fallback}" if fallback else f"{e}"
        return ApplyResult(ok=False, error=msg)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()[:300]
        msg = f"exit {result.returncode}: {err}"
        if fallback:
            msg = f"{msg} — {fallback}"
        return ApplyResult(ok=False, error=msg)
    return ApplyResult(ok=True, diff=result.stdout.strip()[:500] or None)


def _exec_check(action: Action, schema_version: str) -> ApplyResult:
    check = action.payload["check"]
    if check == "lore_on_path":
        ok, msg = check_lore_on_path()
        return ApplyResult(ok=ok, error=None if ok else msg)
    if check == "lore_version_match":
        ok, msg = check_lore_version_match(action.payload.get("lore_repo"))
        # Surface the message even when ok so the user sees the version
        # they're running.
        return ApplyResult(ok=ok, error=None if ok else msg, diff=msg if ok else None)
    if check == "binary_on_path":
        bin_name = action.payload["args"]["binary"]
        if shutil.which(bin_name):
            return ApplyResult(ok=True)
        return ApplyResult(
            ok=False,
            error=action.payload.get("fail_message", f"{bin_name} not on PATH"),
        )
    if check == "always_advisory":
        # Surfaces the fail_message as informational; paired with
        # on_failure="continue" at the call site.
        return ApplyResult(
            ok=False, error=action.payload.get("fail_message", "advisory")
        )
    return ApplyResult(ok=False, error=f"unknown check: {check}")


def _exec_delete(action: Action, schema_version: str) -> ApplyResult:
    real = _real_path(Path(action.payload["path"]).expanduser())
    kp = action.payload.get("key_path")
    if kp:
        if real.exists():
            managed_files.json_merge_atomic(real, _del_key(list(kp)))
        return ApplyResult(ok=True)
    if action.payload.get("recursive"):
        # Only lore-managed plugin trees (sentinel present) may be removed.
        managed_files.remove_managed_dir(real)
        return ApplyResult(ok=True)
    if real.exists():
        # Managed block first, else the whole file.
        if managed_files.managed_block_content(real) is not None:
            managed_files.remove_managed_block(real)
        else:
            real.unlink()
    return ApplyResult(ok=True)


_EXECUTORS = {
    KIND_NEW: _exec_new,
    KIND_MERGE: _exec_merge,
    KIND_REPLACE: _exec_replace,
    KIND_RUN: _exec_run,
    KIND_CHECK: _exec_check,
    KIND_DELETE: _exec_delete,
}


def execute_action(action: Action, *, schema_version: str = "1") -> ApplyResult:
    """Apply an Action; idempotent for kinds that should be."""
    executor = _EXECUTORS.get(action.kind)
    if executor is None:
        return ApplyResult(ok=False, error=f"unknown action kind: {action.kind}")
    try:
        return executor(action, schema_version)
    except (managed_files.MalformedConfigError, managed_files.ConcurrentEditError) as e:
        return ApplyResult(ok=False, error=str(e))
    except Exception as e:  # noqa: BLE001
        return ApplyResult(ok=False, error=f"{type(e).__name__}: {e}")


def _undo_new(action: Action) -> ApplyResult:
    real = _real_path(Path(action.payload["path"]).expanduser())
    if action.payload.get("copy_from"):
        # Tree-copy undo: remove the dst tree if it's still lore-managed
        # (parent sentinel present). Only the dst dir goes, not the
        # plugin root.
        if real.is_dir() and (real.parent / managed_files.PLUGIN_SENTINEL).exists():
            shutil.rmtree(real)
        return ApplyResult(ok=True)
    if real.exists():
        # Managed markers mean the file is shared with the user: strip
        # only our block. Otherwise the file is ours and goes entirely.
        if managed_files.managed_block_content(real) is not None:
            managed_files.remove_managed_block(real)
        else:
            real.unlink()
    return ApplyResult(ok=True)


def _undo_merge(action: Action) -> ApplyResult:
    real = _real_path(Path(action.payload["path"]).expanduser())
    if real.exists():
        managed_files.json_merge_atomic(
            real, _del_key(list(action.payload["key_path"]))
        )
    return ApplyResult(ok=True)


def undo_action(action: Action) -> ApplyResult:
    """Reverse an Action — semantic removal of Lore-managed entries.

    Honest contract: keys-Lore-added are absent. Does NOT promise
    byte-equivalent file state. User-edited entries are warn-and-
    remove unless `--no-clobber-edits` was passed (handled by
    dispatcher; this function always removes).

    Undoing a run/check is a no-op: the integration's own undo handles the
    side effect (e.g. `claude plugin uninstall` is its own action, not a
    reverse of `claude plugin install`).
    """
    try:
        if action.kind == KIND_NEW:
            return _undo_new(action)
        if action.kind in (KIND_MERGE, KIND_REPLACE):
            return _undo_merge(action)
        if action.kind in (KIND_RUN, KIND_CHECK):
            return ApplyResult(ok=True)
    except Exception as e:  # noqa: BLE001
        return ApplyResult(ok=False, error=f"{type(e).__name__}: {e}")
    return ApplyResult(ok=False, error=f"unknown action kind: {action.kind}")
