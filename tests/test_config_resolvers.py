"""Tests for ``lore_core.config`` resolvers — ``get_lore_root`` (silent
default) and ``require_lore_root`` (strict).

Phase 2 of the cleanup roadmap added ``require_lore_root`` to give CLI
entrypoints a single, typed way to fail fast when ``LORE_ROOT`` is
unset or missing — replacing a 4-line pattern that had been
copy-pasted across five ``*_cmd`` files with subtly different exit
codes and error messages.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lore_core.config import (
    LoreRootMissing,
    LoreRootNotSet,
    get_lore_root,
    get_wiki_root,
    require_lore_root,
)


# ---- get_lore_root: silent default ----


def test_get_lore_root_uses_env_when_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    assert get_lore_root() == tmp_path.resolve()


def test_get_lore_root_falls_back_to_home_lore_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LORE_ROOT", raising=False)
    result = get_lore_root()
    # Compare via resolve() — both paths normalise to the same canonical form.
    assert result == (Path.home() / "lore").resolve()


def test_get_wiki_root_is_lore_root_plus_wiki(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    assert get_wiki_root() == (tmp_path / "wiki").resolve()


# ---- require_lore_root: strict ----


def test_require_lore_root_returns_path_when_env_set_and_dir_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    assert require_lore_root() == tmp_path.resolve()


def test_require_lore_root_raises_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LORE_ROOT", raising=False)
    with pytest.raises(LoreRootNotSet):
        require_lore_root()


def test_require_lore_root_raises_when_env_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LORE_ROOT", "")
    with pytest.raises(LoreRootNotSet):
        require_lore_root()


def test_require_lore_root_raises_when_path_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ghost = tmp_path / "ghost-vault"
    monkeypatch.setenv("LORE_ROOT", str(ghost))
    with pytest.raises(LoreRootMissing) as exc_info:
        require_lore_root()
    # Carry the offending path on the exception so error renderers can
    # show it without re-reading the env.
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
    """The two specific exceptions both inherit from ``LoreRootError`` so
    callers that don't care which failure mode happened can catch one type."""
    from lore_core.config import LoreRootError

    assert issubclass(LoreRootNotSet, LoreRootError)
    assert issubclass(LoreRootMissing, LoreRootError)
