"""Tests for `lore_core.install._helpers` — the JSON merge, flock,
managed-markers, pipx cascade, per-platform path resolution, and
content-hash primitives that everything else builds on.
"""

from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path

import pytest
from lore_core.install import _helpers
from lore_core.install._helpers import (
    MANAGED_BLOCK_END,
    MANAGED_BLOCK_START,
    MalformedConfigError,
    content_hash,
    cursor_config_dir,
    cursor_rules_dir,
    install_self_via,
    json_merge_atomic,
    lore_mcp_entry,
    managed_block_content,
    remove_managed_block,
    write_managed_markdown,
)


# ---------------------------------------------------------------------------
# JSON merge — atomicity + flock + validate-after-write
# ---------------------------------------------------------------------------


def test_json_merge_creates_file_when_absent(tmp_path):
    target = tmp_path / "config.json"
    json_merge_atomic(
        target, mutator=lambda d: {**d, "foo": "bar"}
    )
    assert json.loads(target.read_text()) == {"foo": "bar"}


def test_json_merge_preserves_existing_keys(tmp_path):
    target = tmp_path / "config.json"
    target.write_text(json.dumps({"existing": 1, "shared": "old"}))
    json_merge_atomic(
        target,
        mutator=lambda d: {**d, "shared": "new", "added": 2},
    )
    data = json.loads(target.read_text())
    assert data == {"existing": 1, "shared": "new", "added": 2}


def test_json_merge_refuses_malformed_json(tmp_path):
    target = tmp_path / "config.json"
    target.write_text("{not valid json")
    with pytest.raises(MalformedConfigError, match="not valid JSON"):
        json_merge_atomic(target, mutator=lambda d: d)


def test_json_merge_refuses_non_object_root(tmp_path):
    target = tmp_path / "config.json"
    target.write_text("[1, 2, 3]")
    with pytest.raises(MalformedConfigError, match="must be a JSON object"):
        json_merge_atomic(target, mutator=lambda d: d)


def test_json_merge_resolves_symlinks_before_writing(tmp_path):
    """When the target is a symlink (chezmoi/Stow case), the write must
    hit the realpath, not replace the symlink with a regular file."""
    real = tmp_path / "real.json"
    real.write_text(json.dumps({"x": 1}))
    link = tmp_path / "link.json"
    link.symlink_to(real)
    json_merge_atomic(link, mutator=lambda d: {**d, "y": 2})
    # The link is still a symlink
    assert link.is_symlink()
    assert json.loads(real.read_text()) == {"x": 1, "y": 2}


def test_json_merge_validate_after_write_passes(tmp_path):
    target = tmp_path / "config.json"
    json_merge_atomic(
        target,
        mutator=lambda d: {**d, "foo": "bar"},
        validate=lambda d: "foo" in d,
    )
    assert json.loads(target.read_text())["foo"] == "bar"


# ---------------------------------------------------------------------------
# Managed markdown — paired markers + user content preservation
# ---------------------------------------------------------------------------


def test_write_managed_markdown_wraps_content(tmp_path):
    target = tmp_path / "rules.md"
    write_managed_markdown(target, "rule line 1\nrule line 2")
    text = target.read_text()
    assert MANAGED_BLOCK_START in text
    assert MANAGED_BLOCK_END in text
    assert "rule line 1" in text
    assert "rule line 2" in text


def test_managed_block_content_extracts_only_managed_range(tmp_path):
    target = tmp_path / "rules.md"
    target.write_text(
        f"# pre-managed user content\n\n{MANAGED_BLOCK_START}\nlore directive\n{MANAGED_BLOCK_END}\n\n# post-managed user content\n"
    )
    assert managed_block_content(target) == "lore directive"


def test_remove_managed_block_preserves_user_content(tmp_path):
    target = tmp_path / "rules.md"
    target.write_text(
        f"# pre-managed user content\n\n{MANAGED_BLOCK_START}\nlore directive\n{MANAGED_BLOCK_END}\n\n# post-managed user content\n"
    )
    removed = remove_managed_block(target)
    assert removed is True
    text = target.read_text()
    assert "pre-managed user content" in text
    assert "post-managed user content" in text
    assert "lore directive" not in text
    assert MANAGED_BLOCK_START not in text


def test_remove_managed_block_deletes_file_if_only_managed_content(tmp_path):
    target = tmp_path / "rules.md"
    write_managed_markdown(target, "lore directive only")
    removed = remove_managed_block(target)
    assert removed is True
    assert not target.exists()


def test_remove_managed_block_noop_when_no_markers(tmp_path):
    target = tmp_path / "rules.md"
    target.write_text("# user-authored, no managed block\n")
    removed = remove_managed_block(target)
    assert removed is False
    assert target.read_text() == "# user-authored, no managed block\n"


# ---------------------------------------------------------------------------
# install_self_via — pipx → uv → pip cascade
# ---------------------------------------------------------------------------


def test_install_self_via_picks_pipx_when_available(monkeypatch):
    monkeypatch.setattr(
        _helpers.shutil,
        "which",
        lambda b: "/fake/pipx" if b == "pipx" else None,
    )
    name, argv = install_self_via()
    assert name == "pipx"
    assert argv[:2] == ["pipx", "install"]
    # PyPI name `lore` is squatted; canonical non-editable install
    # uses the git+ URL until we pick a clean PyPI name.
    assert argv[-1].startswith("git+https://github.com/buchbend/lore")


def test_install_self_via_falls_back_to_uv_when_no_pipx(monkeypatch):
    monkeypatch.setattr(
        _helpers.shutil, "which", lambda b: f"/fake/{b}" if b == "uv" else None
    )
    name, argv = install_self_via()
    assert name == "uv"
    assert argv[:3] == ["uv", "tool", "install"]


def test_install_self_via_falls_back_to_pip_when_no_pipx_no_uv(monkeypatch):
    monkeypatch.setattr(
        _helpers.shutil, "which", lambda b: f"/fake/{b}" if b == "pip" else None
    )
    name, argv = install_self_via()
    assert name == "pip"
    assert "--user" in argv


def test_install_self_via_raises_when_nothing_available(monkeypatch):
    monkeypatch.setattr(_helpers.shutil, "which", lambda b: None)
    with pytest.raises(RuntimeError, match="No Python installer found"):
        install_self_via()


def test_install_self_via_with_target_uses_editable(monkeypatch, tmp_path):
    monkeypatch.setattr(
        _helpers.shutil, "which", lambda b: "/fake/pipx" if b == "pipx" else None
    )
    name, argv = install_self_via(target=tmp_path)
    assert "--editable" in argv
    assert str(tmp_path) in argv


# ---------------------------------------------------------------------------
# Per-platform path resolution
# ---------------------------------------------------------------------------


def test_cursor_config_dir_macos(monkeypatch):
    monkeypatch.setattr(_helpers.sys, "platform", "darwin")
    path = cursor_config_dir()
    assert "Library/Application Support/Cursor/User" in str(path)


def test_cursor_config_dir_linux_xdg(monkeypatch, tmp_path):
    """When XDG path exists, prefer it over legacy ~/.cursor/."""
    monkeypatch.setattr(_helpers.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(_helpers.Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / "xdg" / "Cursor" / "User").mkdir(parents=True)
    path = cursor_config_dir()
    assert path == tmp_path / "xdg" / "Cursor" / "User"


def test_cursor_config_dir_linux_legacy_fallback(monkeypatch, tmp_path):
    """No XDG path but ~/.cursor/ exists → use legacy."""
    monkeypatch.setattr(_helpers.sys, "platform", "linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(_helpers.Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / ".cursor").mkdir()
    path = cursor_config_dir()
    assert path == tmp_path / ".cursor"


def test_cursor_config_dir_windows_refuses(monkeypatch):
    monkeypatch.setattr(_helpers.sys, "platform", "win32")
    with pytest.raises(NotImplementedError, match="Windows"):
        cursor_config_dir()


def test_claude_config_dir_windows_refuses(monkeypatch):
    monkeypatch.setattr(_helpers.sys, "platform", "win32")
    with pytest.raises(NotImplementedError, match="Windows"):
        _helpers.claude_config_dir()


# ---------------------------------------------------------------------------
# content_hash + lore_mcp_entry
# ---------------------------------------------------------------------------


def test_content_hash_stable():
    assert content_hash("hello") == content_hash("hello")
    assert content_hash("hello") != content_hash("world")
    # Sanity: SHA-256 hex is 64 chars
    assert len(content_hash("x")) == 64


def test_lore_mcp_entry_includes_schema_version():
    entry = lore_mcp_entry("1")
    assert entry["command"] == "lore"
    assert entry["args"] == ["mcp"]
    assert entry["_lore_schema_version"] == "1"
