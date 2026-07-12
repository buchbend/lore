"""Locate the Lore source tree and reconcile it with the installed package.

The plugin (skills, hooks, MCP wiring) and the Python `lore` binary are on
separate update channels: `claude plugin update` refreshes one, `pipx
install` the other. Anything that needs to answer "which Lore is actually
running here?" — the SessionStart banner, `lore doctor`, the Cursor
installer — resolves the on-disk source root through this module and
compares it against the installed distribution.

Stdlib-only. Imported by `lore_cli` and `lore_core.install` alike, so it
must stay free of both.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def resolve_lore_source_root(lore_repo: Path | None = None) -> Path | None:
    """Locate the lore source-of-truth root containing skills/ + .claude-plugin/.

    Resolution order:
      1. ``lore_repo`` (passed by ``lore install --lore-repo`` for dev installs)
      2. Walk up from this file looking for a directory containing both
         ``skills/`` and ``.claude-plugin/plugin.json`` (editable pip install)
      3. ``~/.claude/plugins/cache/lore/lore/<version>/`` (Claude Code
         marketplace install — newest version dir wins)

    Returns ``None`` if nothing resolves; callers decide whether that's
    fatal or a warning.
    """
    if lore_repo:
        candidate = Path(lore_repo).expanduser().resolve()
        if _is_source_root(candidate):
            return candidate
    here = Path(__file__).resolve()
    for parent in here.parents:
        if _is_source_root(parent):
            return parent
    return _newest_cached_plugin()


def _is_source_root(path: Path) -> bool:
    """A source root ships both the skills tree and the plugin manifest."""
    return (path / "skills").is_dir() and (
        path / ".claude-plugin" / "plugin.json"
    ).is_file()


def _newest_cached_plugin() -> Path | None:
    """Newest usable version dir under Claude Code's marketplace plugin cache."""
    cache_root = Path.home() / ".claude" / "plugins" / "cache" / "lore" / "lore"
    if not cache_root.is_dir():
        return None
    version_dirs = sorted(
        (d for d in cache_root.iterdir() if d.is_dir()),
        key=lambda d: d.name,
        reverse=True,
    )
    for version_dir in version_dirs:
        if _is_source_root(version_dir):
            return version_dir
    return None


def read_claude_manifest(source_root: Path) -> dict[str, Any]:
    """Read ``.claude-plugin/plugin.json`` from a resolved source root."""
    return json.loads(
        (source_root / ".claude-plugin" / "plugin.json").read_text()
    )


def check_lore_version_match(
    lore_repo: Path | str | None = None,
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
