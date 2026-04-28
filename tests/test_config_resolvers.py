"""Tests for ``lore_core.config`` resolvers — env / config-file / default
precedence and all four public entrypoints.

Issue #6 added a config-file fallback at ``~/.config/lore/config.yml``
(XDG-aware). Resolution order: env (whitespace-stripped, non-empty) →
config-file → ``~/lore`` default.

The resolvers are:

- ``get_lore_root()`` — silent default
- ``resolve_lore_root()`` — ``Path | None`` (None when unconfigured)
- ``require_lore_root()`` — strict, raises
- ``lore_root_source()`` — debug-only label

The autouse ``_isolate_user_config`` fixture in ``conftest.py`` fakes
``$HOME`` and clears ``$XDG_CONFIG_HOME`` so these tests never read the
developer's real config file.
"""
from __future__ import annotations

import os
import stat
import sys
import warnings
from pathlib import Path

import pytest

from lore_core.config import (
    LoreRootError,
    LoreRootMissing,
    LoreRootNotConfigured,
    LoreRootNotSet,
    get_lore_root,
    get_wiki_root,
    lore_root_source,
    require_lore_root,
    resolve_lore_root,
    user_config_path,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_config(home: Path, content: str) -> Path:
    """Write a config file under ``$HOME/.config/lore/config.yml``."""
    cfg_dir = home / ".config" / "lore"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg = cfg_dir / "config.yml"
    cfg.write_text(content)
    return cfg


# ---------------------------------------------------------------------------
# get_lore_root: silent default
# ---------------------------------------------------------------------------


def test_get_lore_root_uses_env_when_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    assert get_lore_root() == tmp_path.resolve()


def test_get_lore_root_falls_back_to_home_lore_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LORE_ROOT", raising=False)
    result = get_lore_root()
    assert result == (Path.home() / "lore").resolve()


def test_get_wiki_root_is_lore_root_plus_wiki(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    assert get_wiki_root() == (tmp_path / "wiki").resolve()


# ---------------------------------------------------------------------------
# Precedence: env > config > default
# ---------------------------------------------------------------------------


def test_get_lore_root_env_wins_over_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_root = tmp_path / "env-vault"
    config_root = tmp_path / "config-vault"
    env_root.mkdir()
    config_root.mkdir()
    monkeypatch.setenv("LORE_ROOT", str(env_root))
    _write_config(Path.home(), f"lore_root: {config_root}\n")
    assert get_lore_root() == env_root.resolve()


def test_get_lore_root_config_wins_over_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_root = tmp_path / "config-vault"
    config_root.mkdir()
    monkeypatch.delenv("LORE_ROOT", raising=False)
    _write_config(Path.home(), f"lore_root: {config_root}\n")
    assert get_lore_root() == config_root.resolve()


# ---------------------------------------------------------------------------
# Whitespace stripping in LORE_ROOT
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("env_value,expect_default", [
    ("   ", True),
    ("", True),
    ("\t\n", True),
])
def test_get_lore_root_strips_whitespace_treats_blank_as_unset(
    env_value: str, expect_default: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LORE_ROOT", env_value)
    if expect_default:
        assert get_lore_root() == (Path.home() / "lore").resolve()


def test_get_lore_root_strips_whitespace_around_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LORE_ROOT", f"  {tmp_path}  ")
    assert get_lore_root() == tmp_path.resolve()


# ---------------------------------------------------------------------------
# resolve_lore_root: None when unconfigured
# ---------------------------------------------------------------------------


def test_resolve_lore_root_returns_none_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LORE_ROOT", raising=False)
    # No config file written.
    assert resolve_lore_root() is None


def test_resolve_lore_root_returns_path_for_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    assert resolve_lore_root() == tmp_path.resolve()


def test_resolve_lore_root_returns_path_for_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LORE_ROOT", raising=False)
    _write_config(Path.home(), f"lore_root: {tmp_path}\n")
    assert resolve_lore_root() == tmp_path.resolve()


# ---------------------------------------------------------------------------
# lore_root_source: debug labels
# ---------------------------------------------------------------------------


def test_lore_root_source_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    assert lore_root_source() == "env"


def test_lore_root_source_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LORE_ROOT", raising=False)
    _write_config(Path.home(), f"lore_root: {tmp_path}\n")
    assert lore_root_source() == "config"


def test_lore_root_source_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LORE_ROOT", raising=False)
    assert lore_root_source() == "default"


# ---------------------------------------------------------------------------
# XDG_CONFIG_HOME handling
# ---------------------------------------------------------------------------


def test_xdg_config_home_honoured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When XDG_CONFIG_HOME is an absolute path, the config file lives there."""
    xdg_root = tmp_path / "xdg"
    xdg_root.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_root))
    monkeypatch.delenv("LORE_ROOT", raising=False)
    cfg_dir = xdg_root / "lore"
    cfg_dir.mkdir()
    vault = tmp_path / "vault"
    vault.mkdir()
    (cfg_dir / "config.yml").write_text(f"lore_root: {vault}\n")
    assert user_config_path() == cfg_dir / "config.yml"
    assert get_lore_root() == vault.resolve()


@pytest.mark.parametrize("xdg_value", ["", "   ", "relative/path", "."])
def test_xdg_config_home_invalid_falls_back_to_home_config(
    xdg_value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Per spec: empty / whitespace / relative XDG falls back to ~/.config."""
    monkeypatch.setenv("XDG_CONFIG_HOME", xdg_value)
    expected = Path.home() / ".config" / "lore" / "config.yml"
    assert user_config_path() == expected


# ---------------------------------------------------------------------------
# Config file: tilde expansion + value handling
# ---------------------------------------------------------------------------


def test_config_tilde_expansion(monkeypatch: pytest.MonkeyPatch) -> None:
    """``lore_root: "~/v"`` → ``$HOME/v``."""
    monkeypatch.delenv("LORE_ROOT", raising=False)
    home = Path.home()
    vault = home / "tilde-vault"
    vault.mkdir()
    _write_config(home, 'lore_root: "~/tilde-vault"\n')
    assert get_lore_root() == vault.resolve()


def test_config_strips_whitespace_around_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LORE_ROOT", raising=False)
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_config(Path.home(), f'lore_root: "  {vault}  "\n')
    assert get_lore_root() == vault.resolve()


@pytest.mark.parametrize("yaml_content,expect_warning", [
    ('lore_root: ""\n', False),               # empty string → silent
    ('lore_root: "   "\n', False),            # whitespace-only → silent (strips to empty)
    ('lore_root:\n', False),                  # null → silent
    ('other_key: foo\n', True),               # missing key + unknown key → warn (unknown)
    ('lore_root: 42\n', True),                # int → warn
    ('lore_root: [a, b]\n', True),            # list → warn
    ('lore_root:\n  nested: thing\n', True),  # mapping → warn
])
def test_config_unusable_value_falls_back(
    yaml_content: str,
    expect_warning: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All unusable values (empty/null/missing/wrong-type) fall back to default.

    Empty/null/missing are silent (file may exist for future keys);
    non-string values warn.
    """
    monkeypatch.delenv("LORE_ROOT", raising=False)
    _write_config(Path.home(), yaml_content)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = get_lore_root()
    assert result == (Path.home() / "lore").resolve()
    if expect_warning:
        assert len(caught) >= 1
    else:
        assert len([w for w in caught if "config:" in str(w.message)]) == 0


def test_config_unknown_keys_warn_but_dont_break(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unknown top-level keys warn but ``lore_root`` still resolves."""
    monkeypatch.delenv("LORE_ROOT", raising=False)
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_config(Path.home(), f"lore_root: {vault}\nfuture_key: x\n")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = get_lore_root()
    assert result == vault.resolve()
    assert any("unknown key 'future_key'" in str(w.message) for w in caught)


def test_config_malformed_yaml_warns_and_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LORE_ROOT", raising=False)
    _write_config(Path.home(), "lore_root: [unclosed\n")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = get_lore_root()
    assert result == (Path.home() / "lore").resolve()
    assert any("malformed YAML" in str(w.message) for w in caught)


def test_config_top_level_not_mapping_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LORE_ROOT", raising=False)
    _write_config(Path.home(), "- a\n- b\n")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = get_lore_root()
    assert result == (Path.home() / "lore").resolve()
    assert any("top-level must be a mapping" in str(w.message) for w in caught)


def test_config_yaml_safe_load_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Construct a YAML doc with a !!python/object tag — `safe_load` rejects
    it; `load` would execute it. Assert no shell command runs and no
    crash."""
    monkeypatch.delenv("LORE_ROOT", raising=False)
    canary = tmp_path / "canary"
    # If yaml.load were used, this would create the canary file.
    payload = (
        "lore_root: !!python/object/apply:os.system\n"
        f"  - 'touch {canary}'\n"
    )
    _write_config(Path.home(), payload)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = get_lore_root()
    assert result == (Path.home() / "lore").resolve()
    assert not canary.exists(), "yaml.load was used — RCE!"
    assert any("malformed YAML" in str(w.message) for w in caught)


def test_config_file_is_directory_handled(monkeypatch: pytest.MonkeyPatch) -> None:
    """``mkdir -p ~/.config/lore/config.yml`` — read raises IsADirectoryError."""
    monkeypatch.delenv("LORE_ROOT", raising=False)
    cfg_dir = Path.home() / ".config" / "lore" / "config.yml"
    cfg_dir.mkdir(parents=True)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = get_lore_root()
    assert result == (Path.home() / "lore").resolve()
    assert any("cannot read" in str(w.message) for w in caught)


@pytest.mark.skipif(sys.platform.startswith("win"), reason="chmod doesn't apply on Windows")
def test_config_file_unreadable_handled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LORE_ROOT", raising=False)
    cfg = _write_config(Path.home(), "lore_root: /tmp/v\n")
    cfg.chmod(0)
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = get_lore_root()
        assert result == (Path.home() / "lore").resolve()
        assert any("cannot read" in str(w.message) for w in caught)
    finally:
        cfg.chmod(stat.S_IRUSR | stat.S_IWUSR)  # restore for cleanup


# ---------------------------------------------------------------------------
# require_lore_root: strict
# ---------------------------------------------------------------------------


def test_require_lore_root_returns_path_when_env_set_and_dir_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    assert require_lore_root() == tmp_path.resolve()


def test_require_lore_root_accepts_config_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LORE_ROOT", raising=False)
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_config(Path.home(), f"lore_root: {vault}\n")
    assert require_lore_root() == vault.resolve()


def test_require_lore_root_raises_not_configured_when_neither_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LORE_ROOT", raising=False)
    with pytest.raises(LoreRootNotConfigured):
        require_lore_root()


def test_require_lore_root_raises_when_env_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LORE_ROOT", "")
    with pytest.raises(LoreRootNotConfigured):
        require_lore_root()


def test_require_lore_root_raises_when_env_whitespace_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LORE_ROOT", "   ")
    with pytest.raises(LoreRootNotConfigured):
        require_lore_root()


def test_require_lore_root_raises_missing_when_env_path_does_not_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ghost = tmp_path / "ghost-vault"
    monkeypatch.setenv("LORE_ROOT", str(ghost))
    with pytest.raises(LoreRootMissing) as exc_info:
        require_lore_root()
    assert exc_info.value.path == ghost.resolve()


def test_require_lore_root_raises_missing_when_config_path_does_not_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LORE_ROOT", raising=False)
    ghost = tmp_path / "ghost-vault"
    _write_config(Path.home(), f"lore_root: {ghost}\n")
    with pytest.raises(LoreRootMissing) as exc_info:
        require_lore_root()
    assert exc_info.value.path == ghost.resolve()


def test_require_lore_root_handles_tilde_expansion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Env value with ``~`` is expanded before existence check."""
    monkeypatch.setenv("HOME", str(tmp_path))
    real = tmp_path / "vault"
    real.mkdir()
    monkeypatch.setenv("LORE_ROOT", "~/vault")
    assert require_lore_root() == real.resolve()


def test_lore_root_errors_share_a_common_base() -> None:
    assert issubclass(LoreRootNotConfigured, LoreRootError)
    assert issubclass(LoreRootMissing, LoreRootError)


def test_deprecated_lorerootnotset_alias() -> None:
    """``LoreRootNotSet`` is the deprecated alias for ``LoreRootNotConfigured``."""
    assert LoreRootNotSet is LoreRootNotConfigured
