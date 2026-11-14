"""A pipx install ships skills/ and the plugin manifest inside lore_core.

An OpenCode-only person has no Claude Code plugin cache and no checkout.
`resolve_lore_source_root` then falls back to the copy bundled in the wheel
(`lore_core/bundled`), so `lore setup` can write the OpenCode plugin.
"""

from __future__ import annotations

from pathlib import Path

import lore_core
from lore_core import source_root


def test_bundled_copy_is_a_source_root():
    bundled = Path(lore_core.__file__).parent / "bundled"
    assert source_root._is_source_root(bundled)


def test_resolve_falls_back_to_the_bundled_copy(monkeypatch, tmp_path):
    monkeypatch.setattr(source_root, "_is_source_root", _only_bundled)
    monkeypatch.setattr(source_root, "_newest_cached_plugin", lambda: None)
    got = source_root.resolve_lore_source_root()
    assert got == Path(lore_core.__file__).parent / "bundled"


_real = source_root._is_source_root


def _only_bundled(path: Path) -> bool:
    return path.name == "bundled" and _real(path)
