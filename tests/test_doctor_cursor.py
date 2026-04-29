"""Tests for the Cursor-specific advisory checks added to ``lore doctor``.

All three checks are gated on ``~/.cursor/`` existing — Claude-only
users should see them skip cleanly. When Cursor is present, the checks
catch:

  * plugin manifest version drift after a pipx upgrade
  * sticky-abs-path drift in the plugin-local mcp.json
  * malformed / empty hooks.json

Each test fakes ``Path.home()`` (and ``importlib.metadata.version``
where relevant) so the suite is hermetic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from lore_cli import doctor_cmd
from lore_core.install._helpers import PLUGIN_SENTINEL


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Fake $HOME so ~/.cursor lookups land in tmp_path."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


@pytest.fixture
def cursor_dir(fake_home):
    """Bare ~/.cursor/ — represents an installed-but-unconfigured Cursor."""
    cdir = fake_home / ".cursor"
    cdir.mkdir()
    return cdir


@pytest.fixture
def healthy_cursor_install(cursor_dir, monkeypatch):
    """Materialize a plugin dir that the doctor checks would pass."""
    plugin_dir = cursor_dir / "plugins" / "local" / "lore"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / PLUGIN_SENTINEL).write_text("# lore-managed\n")
    (plugin_dir / ".cursor-plugin").mkdir()
    (plugin_dir / ".cursor-plugin" / "plugin.json").write_text(
        json.dumps({"name": "lore", "version": "9.9.9"})
    )
    # mcp.json with abs path to the running interpreter (which we know
    # exists and is executable on the test host).
    import sys
    (plugin_dir / "mcp.json").write_text(json.dumps({
        "mcpServers": {
            "lore": {"command": sys.executable, "args": ["mcp"]},
        }
    }))
    (plugin_dir / "hooks.json").write_text(json.dumps({
        "version": 1,
        "hooks": {
            "sessionStart": [{"type": "command", "command": "x"}],
            "stop": [{"type": "command", "command": "y"}],
        }
    }))
    # Pin importlib.metadata.version("lore") so the version check
    # passes — by default it would return the real installed version.
    import importlib.metadata as md
    monkeypatch.setattr(md, "version", lambda name: "9.9.9")
    return plugin_dir


# ---------------------------------------------------------------------------
# Skip behavior — Claude-only users
# ---------------------------------------------------------------------------


def test_skips_when_no_cursor_dir(fake_home):
    ok, msg = doctor_cmd._check_cursor_plugin_dir(str(fake_home))
    assert ok and "skipped" in msg
    ok, msg = doctor_cmd._check_cursor_mcp_command_resolves(str(fake_home))
    assert ok and "skipped" in msg
    ok, msg = doctor_cmd._check_cursor_hooks_config(str(fake_home))
    assert ok and "skipped" in msg


# ---------------------------------------------------------------------------
# Plugin-dir check
# ---------------------------------------------------------------------------


def test_plugin_dir_missing_flagged(cursor_dir):
    """Cursor installed but no plugin dir → flag with install hint."""
    ok, msg = doctor_cmd._check_cursor_plugin_dir(str(cursor_dir))
    assert not ok
    assert "lore install" in msg


def test_plugin_dir_missing_sentinel_flagged(cursor_dir):
    """Plugin dir present without sentinel → flag (predates plugin packaging)."""
    plugin_dir = cursor_dir / "plugins" / "local" / "lore"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / ".cursor-plugin").mkdir()
    (plugin_dir / ".cursor-plugin" / "plugin.json").write_text(
        json.dumps({"name": "lore", "version": "9.9.9"})
    )
    ok, msg = doctor_cmd._check_cursor_plugin_dir(str(cursor_dir))
    assert not ok
    assert PLUGIN_SENTINEL in msg


def test_plugin_dir_version_drift_flagged(cursor_dir, monkeypatch):
    """Manifest version != installed pip version → flag with reinstall hint."""
    plugin_dir = cursor_dir / "plugins" / "local" / "lore"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / PLUGIN_SENTINEL).write_text("# lore-managed\n")
    (plugin_dir / ".cursor-plugin").mkdir()
    (plugin_dir / ".cursor-plugin" / "plugin.json").write_text(
        json.dumps({"name": "lore", "version": "9.9.9"})
    )
    import importlib.metadata as md
    monkeypatch.setattr(md, "version", lambda name: "0.31.0")
    ok, msg = doctor_cmd._check_cursor_plugin_dir(str(cursor_dir))
    assert not ok
    assert "9.9.9" in msg and "0.31.0" in msg


def test_plugin_dir_passes_when_clean(healthy_cursor_install):
    ok, msg = doctor_cmd._check_cursor_plugin_dir(
        str(healthy_cursor_install)
    )
    assert ok
    assert "9.9.9" in msg


# ---------------------------------------------------------------------------
# MCP command resolution check
# ---------------------------------------------------------------------------


def test_mcp_command_missing_file_flagged(cursor_dir):
    plugin_dir = cursor_dir / "plugins" / "local" / "lore"
    plugin_dir.mkdir(parents=True)
    ok, msg = doctor_cmd._check_cursor_mcp_command_resolves(str(cursor_dir))
    assert not ok
    assert "not present" in msg


def test_mcp_command_relative_path_flagged(cursor_dir):
    plugin_dir = cursor_dir / "plugins" / "local" / "lore"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "mcp.json").write_text(json.dumps({
        "mcpServers": {"lore": {"command": "lore", "args": ["mcp"]}}
    }))
    ok, msg = doctor_cmd._check_cursor_mcp_command_resolves(str(cursor_dir))
    assert not ok
    assert "relative" in msg


def test_mcp_command_stale_abs_path_flagged(cursor_dir):
    """Catches the sticky-path footgun: command was abs at install time
    but now points to a no-longer-existing binary (pipx upgraded into
    a different venv UUID, etc.)."""
    plugin_dir = cursor_dir / "plugins" / "local" / "lore"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "mcp.json").write_text(json.dumps({
        "mcpServers": {
            "lore": {
                "command": "/nonexistent/path/lore",
                "args": ["mcp"],
            }
        }
    }))
    ok, msg = doctor_cmd._check_cursor_mcp_command_resolves(str(cursor_dir))
    assert not ok
    assert "no longer exists" in msg
    assert "lore install" in msg


def test_mcp_command_resolves_when_healthy(healthy_cursor_install):
    ok, msg = doctor_cmd._check_cursor_mcp_command_resolves(
        str(healthy_cursor_install)
    )
    assert ok
    assert "resolves" in msg


# ---------------------------------------------------------------------------
# Hooks config check
# ---------------------------------------------------------------------------


def test_hooks_missing_file_flagged(cursor_dir):
    ok, msg = doctor_cmd._check_cursor_hooks_config(str(cursor_dir))
    assert not ok
    assert "not present" in msg


def test_hooks_empty_block_flagged(cursor_dir):
    plugin_dir = cursor_dir / "plugins" / "local" / "lore"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "hooks.json").write_text(json.dumps({"version": 1, "hooks": {}}))
    ok, msg = doctor_cmd._check_cursor_hooks_config(str(cursor_dir))
    assert not ok
    assert "empty" in msg


def test_hooks_wrong_version_flagged(cursor_dir):
    plugin_dir = cursor_dir / "plugins" / "local" / "lore"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "hooks.json").write_text(json.dumps({
        "version": 2,
        "hooks": {"sessionStart": [{"type": "command", "command": "x"}]},
    }))
    ok, msg = doctor_cmd._check_cursor_hooks_config(str(cursor_dir))
    assert not ok
    assert "version" in msg


def test_hooks_passes_when_healthy(healthy_cursor_install):
    ok, msg = doctor_cmd._check_cursor_hooks_config(
        str(healthy_cursor_install)
    )
    assert ok
    assert "sessionStart" in msg
    assert "stop" in msg


# ---------------------------------------------------------------------------
# Detector parity (install_cmd.py)
# ---------------------------------------------------------------------------


def test_install_detects_cursor_via_dir_when_no_cli_on_path(monkeypatch, fake_home):
    """AppImage / .deb / .dmg installs of Cursor have no CLI on PATH —
    the dir-existence detector must catch them."""
    from lore_cli import install_cmd
    # Fake "no CLI on PATH for anything"
    monkeypatch.setattr(install_cmd.shutil, "which", lambda name: None)
    # No ~/.cursor → not detected
    assert install_cmd._integration_present("cursor") is False
    # Create ~/.cursor → detected even with shutil.which returning None
    (fake_home / ".cursor").mkdir()
    assert install_cmd._integration_present("cursor") is True


def test_install_detector_skips_dir_fallback_for_other_integrations(
    monkeypatch, fake_home
):
    """The dir-fallback is cursor-specific; claude must still rely on
    its CLI being on PATH (no analogous Linux-wide marker)."""
    from lore_cli import install_cmd
    monkeypatch.setattr(install_cmd.shutil, "which", lambda name: None)
    (fake_home / ".cursor").mkdir()  # only cursor benefits
    assert install_cmd._integration_present("claude") is False
