"""Shared helpers for the per-integration install modules.

Three groups of utilities:

  Path resolution         per-platform config paths for each integration
  File primitives         atomic JSON merge with flock + retry,
                          markdown-with-managed-markers writer,
                          content-hash for change detection
  Action execution        switch on Action.kind to preview / apply / undo

Everything here is stdlib-only except for `lore_core.io.atomic_write_text`.
No imports from `lore_cli` (the CLI dispatcher imports from us, not
the other way).
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

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

# ---------------------------------------------------------------------------
# Per-platform path resolution
# ---------------------------------------------------------------------------

# `_lore_schema_version` field marker — distinguishes Lore-managed
# blocks from user-authored ones inside shared JSON config files.
SCHEMA_VERSION_KEY = "_lore_schema_version"

# Standard Lore-managed-rules-file marker pair. Anything between these
# two markers is replaced on upgrade and removed on uninstall; anything
# outside the pair is preserved.
MANAGED_BLOCK_START = (
    "<!-- lore-managed-start; uninstall via lore uninstall -->"
)
MANAGED_BLOCK_END = "<!-- lore-managed-end -->"


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


# ---------------------------------------------------------------------------
# File primitives
# ---------------------------------------------------------------------------


def content_hash(text: str) -> str:
    """Stable SHA-256 of UTF-8 bytes, hex digest. Used for change detection."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


@contextlib.contextmanager
def _flocked(path: Path) -> Iterator[None]:
    """fcntl.flock context manager on a sibling lock file.

    We lock a sibling `.lock` file so the lock survives `os.replace`
    (which atomic_write_text does) — locking the target path itself
    would lose the lock when the file is replaced.
    """
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def json_merge_atomic(
    path: Path,
    mutator: callable,  # type: ignore[type-arg]
    validate: callable | None = None,  # type: ignore[type-arg]
) -> dict[str, Any]:
    """Read-modify-write a JSON file under flock, with optional validation.

    `mutator(data: dict) -> dict` returns the new data. If `validate`
    is given, it runs on the freshly-read-back file after write; if
    it returns False, retry the merge once. If still failing, raises
    ConcurrentEditError.

    Resolves symlinks before mutating (so chezmoi/Stow users don't
    have their symlinks replaced with regular files by os.replace).
    Refuses to load malformed JSON — the caller sees a clean error.
    """
    real_path = Path(os.path.realpath(path))
    real_path.parent.mkdir(parents=True, exist_ok=True)

    def _do_one_pass() -> dict[str, Any]:
        data: dict[str, Any]
        if real_path.exists():
            try:
                data = json.loads(real_path.read_text())
            except json.JSONDecodeError as e:
                raise MalformedConfigError(
                    f"{real_path} is not valid JSON: {e}"
                ) from e
            if not isinstance(data, dict):
                raise MalformedConfigError(
                    f"{real_path} root must be a JSON object, got "
                    f"{type(data).__name__}"
                )
        else:
            data = {}
        new_data = mutator(data)
        atomic_write_text(real_path, json.dumps(new_data, indent=2) + "\n")
        return new_data

    with _flocked(real_path):
        result = _do_one_pass()
        if validate is None:
            return result
        if validate(result):
            return result
        # Validate-after-write failed — retry once
        result = _do_one_pass()
        if validate(result):
            return result
        raise ConcurrentEditError(
            f"{real_path} keys missing after write; concurrent edit detected. "
            "Quit Claude Code (or other writer) and retry."
        )


class MalformedConfigError(RuntimeError):
    pass


class ConcurrentEditError(RuntimeError):
    pass


def write_managed_markdown(path: Path, body: str) -> None:
    """Write a markdown file wrapped in lore-managed-start/end markers.

    Atomic. Creates parent dirs. Resolves symlinks before writing.
    """
    real_path = Path(os.path.realpath(path)) if path.exists() else path
    real_path.parent.mkdir(parents=True, exist_ok=True)
    full = (
        f"{MANAGED_BLOCK_START}\n{body.rstrip()}\n{MANAGED_BLOCK_END}\n"
    )
    atomic_write_text(real_path, full)


_MANAGED_BLOCK_RE = re.compile(
    re.escape(MANAGED_BLOCK_START)
    + r"\n(.*?)\n"
    + re.escape(MANAGED_BLOCK_END)
    + r"\n?",
    re.DOTALL,
)


def remove_managed_block(path: Path) -> bool:
    """Remove the lore-managed range from a markdown file.

    Preserves any user content outside the managed markers. Returns
    True if a block was removed, False if the file had no managed
    block (no-op). If no content remains outside the block, the file
    is removed entirely.
    """
    real_path = Path(os.path.realpath(path)) if path.exists() else path
    if not real_path.exists():
        return False
    text = real_path.read_text()
    new_text, n = _MANAGED_BLOCK_RE.subn("", text, count=1)
    if n == 0:
        return False
    new_text = new_text.strip()
    if not new_text:
        real_path.unlink()
    else:
        atomic_write_text(real_path, new_text + "\n")
    return True


def managed_block_content(path: Path) -> str | None:
    """Return the text inside lore-managed markers, or None if absent."""
    if not path.exists():
        return None
    m = _MANAGED_BLOCK_RE.search(path.read_text())
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Self-install + binary-presence checks
# ---------------------------------------------------------------------------

INSTALLERS = ("pipx", "uv", "pip")  # cascade order

# `lore` on PyPI is squatted by an unrelated package (lore 0.8.6 — broken
# on Python 3.13 due to pkg_resources). Until we publish under a different
# name (tracked in an issue), the canonical non-editable install path is
# the GitHub repo.
LORE_GIT_URL = "git+https://github.com/buchbend/lore.git"


def install_self_via(target: Path | None = None) -> tuple[str, list[str]]:
    """Pick the first available installer and return its argv.

    `target` is the editable source path for dev installs (or None to
    install from the GitHub repo via git+ URL — PyPI publish is blocked
    on the `lore` name being squatted). Mirrors install.sh:49–62.

    Returns `(installer_name, argv)`. Caller invokes via subprocess.
    Raises RuntimeError if none are available.
    """
    src = str(target) if target else LORE_GIT_URL
    for installer in INSTALLERS:
        if shutil.which(installer):
            if installer == "pipx":
                argv = ["pipx", "install", "--force"]
                if target:
                    argv += ["--editable"]
                argv.append(src)
            elif installer == "uv":
                argv = ["uv", "tool", "install", "--force"]
                if target:
                    argv += ["--editable"]
                argv.append(src)
            else:  # pip
                argv = ["pip", "install", "--user", "--force-reinstall"]
                if target:
                    argv += ["--editable"]
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


def check_lore_version_match(
    lore_repo: "Path | str | None" = None,
) -> tuple[bool, str]:
    """Compare the installed Python package version against the on-disk source.

    Closes the install-side counterpart to ``tests/test_version_sync.py``:
    that pytest guard catches drift between ``pyproject.toml``,
    ``plugin.json``, and ``CHANGELOG.md`` *in the source tree*; this check
    catches drift between the installed pipx/pip/uv binary and that
    source tree on the user's machine.

    The hook footgun: ``claude plugin update lore@lore`` refreshes the
    Claude Code plugin (skills/hooks/MCP wiring) but does not reinstall
    the Python ``lore`` CLI. SessionStart's status line reads via
    ``importlib.metadata.version(\"lore\")`` — i.e. the installed binary
    — so a stale binary silently shows the old version forever.

    Returns (ok, message). The message includes a copy-pasteable fix
    command tailored to the install method (editable vs. non-editable).
    Returns (True, "...skipped...") when no on-disk source is available
    to compare against (e.g. user installed from PyPI without a clone).
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        installed = version("lore")
    except PackageNotFoundError:
        return False, (
            "lore Python package not installed in this environment. "
            "Run: pipx install --force --editable <path-to-lore-repo>"
        )

    repo_path = Path(lore_repo).expanduser() if lore_repo else None
    if repo_path is None or not (repo_path / "pyproject.toml").is_file():
        return True, f"lore CLI version {installed} (no source tree to compare against)"

    pyproject = repo_path / "pyproject.toml"
    try:
        import tomllib

        on_disk = tomllib.loads(pyproject.read_text())["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        return True, (
            f"lore CLI version {installed} "
            f"(could not read on-disk pyproject.toml: {exc})"
        )

    if installed == on_disk:
        return True, f"lore CLI version {installed} (matches source)"

    # The installed and on-disk versions disagree. Pick the right fix
    # command based on whether the install looks editable or not.
    fix_cmd = f"pipx install --force --editable {repo_path}"
    return False, (
        f"lore CLI version drift: installed {installed}, source at {repo_path} "
        f"is {on_disk}. The Claude Code plugin will silently use the older "
        f"installed binary for `lore hook session-start` etc. Run: {fix_cmd}"
    )


# ---------------------------------------------------------------------------
# Canonical Lore MCP server entry — one source of truth for both integrations.
# ---------------------------------------------------------------------------


def lore_mcp_entry(schema_version: str) -> dict[str, Any]:
    """The mcpServers.lore block we write into shared MCP config files.

    Includes `_lore_schema_version` so future migrations know whether
    the entry is Lore-managed. Underscore prefix discourages user
    edits.
    """
    return {
        "command": "lore",
        "args": ["mcp"],
        SCHEMA_VERSION_KEY: schema_version,
    }


# ---------------------------------------------------------------------------
# Legacy artifact detection
# ---------------------------------------------------------------------------

_LEGACY_HOOK_COMMAND_PREFIX = "lore hook"
_LEGACY_PERMISSION_RULES = {
    "Bash(lore:*)",
    "Bash(lore *)",
}


def detect_install_sh_artifacts(
    lore_repo: Path | None = None,
) -> list[LegacyArtifact]:
    """Scan ~/.claude for install.sh-era state.

    Returns artifacts in a stable order. `lore_repo` filters skill /
    agent symlinks to those pointing into that repo (or any "lore"
    repo if None — symlink target contains `/lore/`).
    """
    artifacts: list[LegacyArtifact] = []
    home = Path.home()

    # 1. Skill symlinks
    skills_dir = home / ".claude" / "skills"
    if skills_dir.is_dir():
        for entry in sorted(skills_dir.iterdir()):
            if not entry.name.startswith("lore:"):
                continue
            if not entry.is_symlink():
                continue
            target = os.readlink(entry)
            if lore_repo is not None and str(lore_repo) not in target:
                continue
            if "/lore/" not in target and lore_repo is None:
                continue
            artifacts.append(
                LegacyArtifact(
                    kind="skill_symlink",
                    path=str(entry),
                    detail=target,
                )
            )

    # 2. Agent symlinks
    agents_dir = home / ".claude" / "agents"
    if agents_dir.is_dir():
        for entry in sorted(agents_dir.iterdir()):
            if not entry.name.startswith("lore-"):
                continue
            if not entry.is_symlink():
                continue
            target = os.readlink(entry)
            if lore_repo is not None and str(lore_repo) not in target:
                continue
            if "/lore/" not in target and lore_repo is None:
                continue
            artifacts.append(
                LegacyArtifact(
                    kind="agent_symlink",
                    path=str(entry),
                    detail=target,
                )
            )

    # 3. settings.json mutations
    settings_path = home / ".claude" / "settings.json"
    if settings_path.exists():
        try:
            cfg = json.loads(settings_path.read_text())
        except json.JSONDecodeError:
            cfg = {}
        # Hook entries
        for event, group_list in (cfg.get("hooks") or {}).items():
            for grp in group_list:
                for h in grp.get("hooks") or []:
                    cmd = h.get("command", "")
                    if isinstance(cmd, str) and cmd.startswith(
                        _LEGACY_HOOK_COMMAND_PREFIX
                    ):
                        artifacts.append(
                            LegacyArtifact(
                                kind="hook_entry",
                                path=str(settings_path),
                                detail=f"{event}: {cmd}",
                            )
                        )
        # Permission rules
        allow = (cfg.get("permissions") or {}).get("allow") or []
        for rule in allow:
            if rule in _LEGACY_PERMISSION_RULES:
                artifacts.append(
                    LegacyArtifact(
                        kind="permission_rule",
                        path=str(settings_path),
                        detail=rule,
                    )
                )
        # LORE_ROOT env entry
        if "LORE_ROOT" in (cfg.get("env") or {}):
            artifacts.append(
                LegacyArtifact(
                    kind="env_entry",
                    path=str(settings_path),
                    detail=f"LORE_ROOT={cfg['env']['LORE_ROOT']}",
                )
            )

    return artifacts


# ---------------------------------------------------------------------------
# Action executors — preview / apply / undo, dispatched on Action.kind
# ---------------------------------------------------------------------------


def preview_action(action: Action) -> str:
    """Return a multi-line diff/preview without side effects."""
    if action.kind == KIND_NEW:
        path = action.payload["path"]
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
        return f"--- {path} (remove)"
    raise ValueError(f"unknown action kind: {action.kind}")


def execute_action(action: Action, *, schema_version: str = "1") -> ApplyResult:
    """Apply an Action; idempotent for kinds that should be."""
    try:
        if action.kind == KIND_NEW:
            path = Path(action.payload["path"]).expanduser()
            content = action.payload["content"]
            real = Path(os.path.realpath(path)) if path.exists() else path
            real.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(real, content)
            return ApplyResult(ok=True)
        if action.kind == KIND_MERGE:
            path = Path(action.payload["path"]).expanduser()
            key_path = list(action.payload["key_path"])
            value = action.payload["value"]

            def _mutator(data: dict) -> dict:
                cur = data
                for key in key_path[:-1]:
                    cur = cur.setdefault(key, {})
                cur[key_path[-1]] = value
                return data

            def _validator(data: dict) -> bool:
                cur = data
                for key in key_path:
                    if not isinstance(cur, dict) or key not in cur:
                        return False
                    cur = cur[key]
                return True

            json_merge_atomic(path, _mutator, validate=_validator)
            return ApplyResult(ok=True)
        if action.kind == KIND_REPLACE:
            # For now treat replace identically to merge; the prompt
            # difference happens at the dispatcher level.
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
        if action.kind == KIND_RUN:
            argv = action.payload["argv"]
            timeout = action.payload.get("timeout", 60)
            try:
                result = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as e:
                fallback = action.payload.get("fallback_message")
                msg = f"{e}"
                if fallback:
                    msg = f"{e} — {fallback}"
                return ApplyResult(ok=False, error=msg)
            if result.returncode != 0:
                fallback = action.payload.get("fallback_message")
                err = (result.stderr or result.stdout or "").strip()[:300]
                msg = f"exit {result.returncode}: {err}"
                if fallback:
                    msg = f"{msg} — {fallback}"
                return ApplyResult(ok=False, error=msg)
            return ApplyResult(ok=True, diff=result.stdout.strip()[:500] or None)
        if action.kind == KIND_CHECK:
            check = action.payload["check"]
            if check == "lore_on_path":
                ok, msg = check_lore_on_path()
                return ApplyResult(ok=ok, error=None if ok else msg)
            if check == "lore_version_match":
                lore_repo = action.payload.get("lore_repo")
                ok, msg = check_lore_version_match(lore_repo)
                # Surface the message even when ok so the user sees
                # the version they're running.
                return ApplyResult(
                    ok=ok,
                    error=None if ok else msg,
                    diff=msg if ok else None,
                )
            if check == "binary_on_path":
                bin_name = action.payload["args"]["binary"]
                if shutil.which(bin_name):
                    return ApplyResult(ok=True)
                return ApplyResult(
                    ok=False,
                    error=action.payload.get(
                        "fail_message", f"{bin_name} not on PATH"
                    ),
                )
            return ApplyResult(ok=False, error=f"unknown check: {check}")
        if action.kind == KIND_DELETE:
            path = Path(action.payload["path"]).expanduser()
            real = Path(os.path.realpath(path)) if path.exists() else path
            kp = action.payload.get("key_path")
            if kp:
                # JSON key removal
                if not real.exists():
                    return ApplyResult(ok=True)  # nothing to remove

                def _mutator(data: dict) -> dict:
                    cur = data
                    for key in kp[:-1]:
                        if not isinstance(cur, dict) or key not in cur:
                            return data
                        cur = cur[key]
                    if isinstance(cur, dict) and kp[-1] in cur:
                        del cur[kp[-1]]
                    return data

                json_merge_atomic(real, _mutator)
                return ApplyResult(ok=True)
            # File removal — managed block first, else whole file
            if real.exists():
                if managed_block_content(real) is not None:
                    remove_managed_block(real)
                else:
                    real.unlink()
            return ApplyResult(ok=True)
    except (MalformedConfigError, ConcurrentEditError) as e:
        return ApplyResult(ok=False, error=str(e))
    except Exception as e:  # noqa: BLE001
        return ApplyResult(ok=False, error=f"{type(e).__name__}: {e}")
    return ApplyResult(ok=False, error=f"unknown action kind: {action.kind}")


def undo_action(action: Action) -> ApplyResult:
    """Reverse an Action — semantic removal of Lore-managed entries.

    Honest contract: keys-Lore-added are absent. Does NOT promise
    byte-equivalent file state. User-edited entries are warn-and-
    remove unless `--no-clobber-edits` was passed (handled by
    dispatcher; this function always removes).
    """
    try:
        if action.kind == KIND_NEW:
            path = Path(action.payload["path"]).expanduser()
            real = Path(os.path.realpath(path)) if path.exists() else path
            if real.exists():
                # Check whether the file uses managed markers; if yes
                # remove just that block, else delete the whole file.
                if managed_block_content(real) is not None:
                    remove_managed_block(real)
                else:
                    real.unlink()
            return ApplyResult(ok=True)
        if action.kind in (KIND_MERGE, KIND_REPLACE):
            path = Path(action.payload["path"]).expanduser()
            key_path = list(action.payload["key_path"])

            def _mutator(data: dict) -> dict:
                cur = data
                for key in key_path[:-1]:
                    if not isinstance(cur, dict) or key not in cur:
                        return data
                    cur = cur[key]
                if isinstance(cur, dict) and key_path[-1] in cur:
                    del cur[key_path[-1]]
                return data

            real = Path(os.path.realpath(path)) if path.exists() else path
            if real.exists():
                json_merge_atomic(real, _mutator)
            return ApplyResult(ok=True)
        if action.kind in (KIND_RUN, KIND_CHECK):
            # Undoing a run/check is a no-op (the integration's own undo
            # handles the side effect — e.g. `claude plugin uninstall`
            # is its own action, not a reverse of `claude plugin
            # install`).
            return ApplyResult(ok=True)
    except Exception as e:  # noqa: BLE001
        return ApplyResult(ok=False, error=f"{type(e).__name__}: {e}")
    return ApplyResult(ok=False, error=f"unknown action kind: {action.kind}")
