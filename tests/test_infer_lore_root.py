"""Tests for ``lore_cli.hooks._infer_lore_root``.

Issue #6 added a config-file fallback at ``~/.config/lore/config.yml``.
The hook helper ``_infer_lore_root`` now resolves with the precedence:

    env → walk-up (wiki/ ancestor) → config-file → ``~/lore`` default

Walk-up beats config-file (but not env) because a path argument is the
explicit signal in a hook context — a user with a global config-file
pointing at ``~/personal-vault`` who is currently editing inside
``~/work-vault/wiki/foo/`` should resolve to ``~/work-vault``.

The function also accepts either a file (CLAUDE.md) or a directory
(cwd) — fixes a latent caller-shape bug at hooks.py:2845 where a
directory was being passed and ``.parent`` skipped a level too high.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lore_cli.hooks import _infer_lore_root


def _make_vault(root: Path, name: str) -> Path:
    """Create a directory with a wiki/ subdir so walk-up can find it."""
    vault = root / name
    (vault / "wiki" / "scratch").mkdir(parents=True)
    return vault


def _write_user_config(home: Path, lore_root: Path) -> None:
    cfg_dir = home / ".config" / "lore"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.yml").write_text(f"lore_root: {lore_root}\n")


def test_infer_lore_root_walk_up_beats_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Multi-vault scenario: config points at vault A, but the hook arg
    is inside vault B → resolve to vault B."""
    monkeypatch.delenv("LORE_ROOT", raising=False)
    personal = _make_vault(tmp_path, "personal-vault")
    work = _make_vault(tmp_path, "work-vault")
    _write_user_config(Path.home(), personal)

    claude_md = work / "wiki" / "scratch" / "CLAUDE.md"
    claude_md.touch()

    result = _infer_lore_root(claude_md)
    assert result == work.resolve()


def test_infer_lore_root_env_beats_walk_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Explicit env-var (per-invocation) overrides walk-up."""
    explicit = _make_vault(tmp_path, "explicit-vault")
    work = _make_vault(tmp_path, "work-vault")
    monkeypatch.setenv("LORE_ROOT", str(explicit))

    claude_md = work / "wiki" / "scratch" / "CLAUDE.md"
    claude_md.touch()

    result = _infer_lore_root(claude_md)
    assert result == explicit.resolve()


def test_infer_lore_root_walk_up_when_no_env_or_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No env, no config — walk up to find the wiki/ ancestor."""
    monkeypatch.delenv("LORE_ROOT", raising=False)
    work = _make_vault(tmp_path, "work-vault")
    claude_md = work / "wiki" / "scratch" / "CLAUDE.md"
    claude_md.touch()

    assert _infer_lore_root(claude_md) == work.resolve()


def test_infer_lore_root_accepts_directory_arg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mirrors the hooks.py:2845 caller shape: pass a cwd, not a CLAUDE.md.

    Pre-fix: the function did ``claude_md_path.parent`` unconditionally,
    so a directory arg would skip one level too high. The fix normalizes
    to "starting directory" via ``start.parent if start.is_file() else start``.
    """
    monkeypatch.delenv("LORE_ROOT", raising=False)
    work = _make_vault(tmp_path, "work-vault")
    cwd = work / "wiki" / "scratch"  # a directory, not a file
    assert cwd.is_dir()

    result = _infer_lore_root(cwd)
    assert result == work.resolve()


def test_infer_lore_root_falls_back_to_config_when_no_walkup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No env, walk-up finds nothing → use config-file value."""
    monkeypatch.delenv("LORE_ROOT", raising=False)
    config_vault = _make_vault(tmp_path, "config-vault")
    _write_user_config(Path.home(), config_vault)

    # An isolated path with no wiki/ ancestor anywhere up the chain.
    isolated = tmp_path / "isolated"
    isolated.mkdir()
    claude_md = isolated / "CLAUDE.md"
    claude_md.touch()

    result = _infer_lore_root(claude_md)
    assert result == config_vault.resolve()


def test_infer_lore_root_falls_back_to_default_when_unconfigured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No env, no walk-up, no config — fall back to ``~/lore`` default."""
    monkeypatch.delenv("LORE_ROOT", raising=False)
    isolated = tmp_path / "isolated"
    isolated.mkdir()
    claude_md = isolated / "CLAUDE.md"
    claude_md.touch()

    result = _infer_lore_root(claude_md)
    assert result == (Path.home() / "lore").resolve()
