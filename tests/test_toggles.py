"""Sentinel-backed per-session mute helpers (`/lore:on`, `/lore:off`).

The `lore_core.toggles` module is the single source of truth for
"is this session muted?" queries used by hooks and the MCP dispatcher.
Sentinel files live in ``$TMPDIR`` so the OS reaps them at session
boundary.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lore_core import toggles


@pytest.fixture(autouse=True)
def _isolate_tmpdir(tmp_path, monkeypatch):
    """Pin TMPDIR per test so sentinels can't leak between tests."""
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    yield


def test_unset_returns_false():
    assert toggles.is_off("all", "sid-abc") is False
    assert toggles.is_off("citations", "sid-abc") is False


def test_set_then_check():
    toggles.set_off("all", "sid-abc")
    assert toggles.is_off("all", "sid-abc") is True


def test_set_is_scope_local():
    toggles.set_off("all", "sid-abc")
    assert toggles.is_off("citations", "sid-abc") is False


def test_set_is_session_local():
    toggles.set_off("all", "sid-abc")
    assert toggles.is_off("all", "sid-other") is False


def test_clear_removes_sentinel():
    toggles.set_off("all", "sid-abc")
    toggles.clear_off("all", "sid-abc")
    assert toggles.is_off("all", "sid-abc") is False


def test_clear_when_unset_is_noop():
    toggles.clear_off("all", "sid-never-set")  # must not raise


def test_set_is_idempotent():
    toggles.set_off("citations", "sid-abc")
    toggles.set_off("citations", "sid-abc")
    assert toggles.is_off("citations", "sid-abc") is True


def test_invalid_scope_raises():
    with pytest.raises(ValueError):
        toggles.set_off("hooks", "sid-abc")
    with pytest.raises(ValueError):
        toggles.is_off("citations-only", "sid-abc")


def test_sentinel_path_is_under_tmpdir(tmp_path):
    toggles.set_off("all", "sid-zzz")
    expected = tmp_path / "lore-off-all-sid-zzz"
    assert expected.exists()


def test_citations_sentinel_has_distinct_path(tmp_path):
    toggles.set_off("citations", "sid-zzz")
    expected = tmp_path / "lore-off-citations-sid-zzz"
    assert expected.exists()


def test_scope_namespaces_do_not_collide(tmp_path):
    """A sid shaped like another scope's prefix must not collide."""
    # 'citations-foo' as a sid, scope='all' → must not match scope='citations', sid='foo'
    toggles.set_off("all", "citations-foo")
    assert toggles.is_off("citations", "foo") is False
    assert toggles.is_off("all", "citations-foo") is True


def test_blank_sid_raises():
    """Empty / missing session id is a programming error — fail loud, not silently file-as-empty."""
    with pytest.raises(ValueError):
        toggles.is_off("all", "")
    with pytest.raises(ValueError):
        toggles.set_off("all", "")
