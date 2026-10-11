"""The install checks behind the Check step of `lore setup`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from lore_cli import checks


@pytest.fixture
def healthy_vault(tmp_path, monkeypatch):
    vault_root = tmp_path / "vault"
    (vault_root / "wiki" / "ccat").mkdir(parents=True)
    monkeypatch.setenv("LORE_ROOT", str(vault_root))
    monkeypatch.setenv("LORE_CACHE", str(tmp_path / "cache"))
    return vault_root


def _run(cwd: Path) -> dict[str, tuple[bool, str]]:
    return {name: fn(str(cwd)) for name, fn, _ in checks._CHECKS}


def test_healthy_vault_passes_the_core_checks(healthy_vault):
    results = _run(healthy_vault)
    for name in ("LORE_ROOT", "wikis", "cache", "MCP server"):
        assert results[name][0] is True, results[name]


def test_no_wikis_fails(tmp_path, monkeypatch):
    vault_root = tmp_path / "vault"
    (vault_root / "wiki").mkdir(parents=True)
    monkeypatch.setenv("LORE_ROOT", str(vault_root))
    ok, msg = checks._check_wikis(str(vault_root))
    assert ok is False
    assert "no wikis" in msg


def test_lore_root_missing_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_ROOT", str(tmp_path / "does-not-exist"))
    ok, _ = checks._check_lore_root(str(tmp_path))
    assert ok is False


# ----- LORE_ROOT source label (issue #6) -----


def test_reports_source_env(healthy_vault):
    """When $LORE_ROOT is set, the check labels the source ``$LORE_ROOT``."""
    ok, msg = checks._check_lore_root(str(healthy_vault))
    assert ok is True
    assert "[$LORE_ROOT]" in msg


def test_reports_source_config(tmp_path, monkeypatch):
    """When config-file is set (env unset), the check labels source ``config-file``."""
    vault = tmp_path / "vault"
    (vault / "wiki" / "ccat").mkdir(parents=True)
    cfg_dir = Path.home() / ".config" / "lore"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.yml").write_text(f"lore_root: {vault}\n")
    monkeypatch.delenv("LORE_ROOT", raising=False)
    ok, msg = checks._check_lore_root(str(vault))
    assert ok is True
    assert "[config-file]" in msg


def test_reports_source_default_when_unconfigured(monkeypatch):
    """Neither env nor config-file: the label is ``unconfigured (fallback)``."""
    monkeypatch.delenv("LORE_ROOT", raising=False)
    (Path.home() / "lore" / "wiki").mkdir(parents=True)
    _, msg = checks._check_lore_root(str(Path.home() / "lore"))
    assert "[unconfigured (fallback)]" in msg


def test_reports_source_even_on_missing_root(tmp_path, monkeypatch):
    """Missing path failure case still includes the source label."""
    monkeypatch.setenv("LORE_ROOT", str(tmp_path / "ghost-vault"))
    ok, msg = checks._check_lore_root(str(tmp_path))
    assert ok is False
    assert "[$LORE_ROOT]" in msg
    assert "does not exist" in msg


# ---------------------------------------------------------------------------
# `_check_claude_plugin_cache_drift` — Claude plugin cache vs pip drift
# ---------------------------------------------------------------------------


def _write_plugin_index(home: Path, *, version: str | None, scope: str = "user") -> None:
    """Write a minimal `~/.claude/plugins/installed_plugins.json`."""
    plugins_dir = home / ".claude" / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    if version is not None:
        entries.append(
            {"scope": scope, "version": version, "installPath": str(plugins_dir / "lore" / version)}
        )
    (plugins_dir / "installed_plugins.json").write_text(
        json.dumps({"version": 2, "plugins": {"lore@lore": entries}})
    )


def test_plugin_cache_drift_skips_when_no_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(checks.Path, "home", classmethod(lambda cls: tmp_path))
    ok, msg = checks._check_claude_plugin_cache_drift(str(tmp_path))
    assert ok is True
    assert "no Claude plugin cache" in msg


def test_plugin_cache_drift_skips_when_lore_not_installed_in_claude(tmp_path, monkeypatch):
    monkeypatch.setattr(checks.Path, "home", classmethod(lambda cls: tmp_path))
    _write_plugin_index(tmp_path, version=None)
    ok, msg = checks._check_claude_plugin_cache_drift(str(tmp_path))
    assert ok is True
    assert "not present" in msg


def test_plugin_cache_drift_passes_when_versions_match(tmp_path, monkeypatch):
    monkeypatch.setattr(checks.Path, "home", classmethod(lambda cls: tmp_path))
    _write_plugin_index(tmp_path, version="0.13.1")
    # Stub importlib.metadata.version to return the matching string.
    import importlib.metadata as _md
    monkeypatch.setattr(_md, "version", lambda _name: "0.13.1")
    ok, msg = checks._check_claude_plugin_cache_drift(str(tmp_path))
    assert ok is True
    assert "0.13.1 matches pip" in msg


def test_plugin_cache_drift_fails_when_pip_lags_behind_cache(tmp_path, monkeypatch):
    """The headline footgun: `claude plugin update` advanced the cache,
    pipx wasn't upgraded, banner silently shows the old version."""
    monkeypatch.setattr(checks.Path, "home", classmethod(lambda cls: tmp_path))
    _write_plugin_index(tmp_path, version="0.13.1")
    import importlib.metadata as _md
    monkeypatch.setattr(_md, "version", lambda _name: "0.10.0")
    ok, msg = checks._check_claude_plugin_cache_drift(str(tmp_path))
    assert ok is False
    assert "0.13.1" in msg
    assert "0.10.0" in msg
    assert "pipx upgrade lore" in msg
    assert "restart Claude Code" in msg


def test_plugin_cache_drift_points_at_the_plugin_when_cache_lags_behind_pip(tmp_path, monkeypatch):
    """The binary was upgraded but the plugin was not: the fix is a plugin
    update and a restart, not another binary upgrade."""
    monkeypatch.setattr(checks.Path, "home", classmethod(lambda cls: tmp_path))
    _write_plugin_index(tmp_path, version="0.74.2")
    import importlib.metadata as _md
    monkeypatch.setattr(_md, "version", lambda _name: "0.75.0")
    ok, msg = checks._check_claude_plugin_cache_drift(str(tmp_path))
    assert ok is False
    assert "claude plugin update lore@lore" in msg
    assert "restart Claude Code" in msg
    assert "pipx" not in msg


def test_plugin_cache_drift_unreadable_index_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(checks.Path, "home", classmethod(lambda cls: tmp_path))
    plugins_dir = tmp_path / ".claude" / "plugins"
    plugins_dir.mkdir(parents=True)
    (plugins_dir / "installed_plugins.json").write_text("{not json")
    ok, msg = checks._check_claude_plugin_cache_drift(str(tmp_path))
    assert ok is False
    assert "unreadable" in msg


def test_plugin_cache_drift_fails_the_overall_run(healthy_vault, tmp_path, monkeypatch):
    """Plugin-cache drift is a *failing* check, not advisory: the Check step
    of `lore setup` reports it as a failure."""
    monkeypatch.setattr(checks.Path, "home", classmethod(lambda cls: tmp_path))
    _write_plugin_index(tmp_path, version="0.13.1")
    import importlib.metadata as _md
    monkeypatch.setattr(_md, "version", lambda _name: "0.10.0")

    from lore_cli.setup_cmd import step_check

    _, events = step_check(healthy_vault)
    assert any(ok is False and msg.startswith("plugin cache:") for ok, msg in events)


def test_doctor_capture_panel_lock_free_removed_smoke(tmp_path):
    """Placeholder so pytest collection still passes; old capture-panel
    tests for free-lock / hook-errors / marker are superseded by
    tests/test_capture_state.py (CaptureState field coverage).
    """
    events = tmp_path / ".lore" / "spine.jsonl"
    events.parent.mkdir(parents=True, exist_ok=True)
    events.write_text(
        json.dumps({
            "ts": "2026-04-20T14:32:05Z", "v": 1, "source": "hook",
            "event": "session-end", "level": "info", "trace_id": None,
            "session_id": None, "run_id": None, "wiki": None, "scope": None,
            "error_code": None, "data": {"outcome": "ledger-advanced"},
        }) + "\n"
    )
    # Post-Task-12a: capture-pipeline details now live in CaptureState /
    # `lore status`; nothing to assert about the doctor panel here.
    # Kept as a smoke fixture so future test additions have a starting
    # point; delete the whole test if it starts rotting.


def test_check_spine_writable_reports_degrade_marker(tmp_path, monkeypatch):
    """AC4: a spine write-failure degrade marker is surfaced by doctor."""
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    (tmp_path / ".lore").mkdir(parents=True, exist_ok=True)

    ok, msg = checks._check_spine_writable(str(tmp_path))
    assert ok is True
    assert "spine" in msg.lower()

    (tmp_path / ".lore" / "spine-failed.marker").touch()
    ok, msg = checks._check_spine_writable(str(tmp_path))
    assert ok is False
    assert "spine write failed" in msg.lower()


def test_check_vale_reports_absence_without_failing(monkeypatch):
    """AC: `lore doctor` reports Vale's absence but never fails the run —
    the check is registered advisory (fails_run=False)."""
    monkeypatch.setattr(checks.shutil, "which", lambda name: None)
    ok, msg = checks._check_vale(".")
    assert ok is False
    assert "vale" in msg.lower()
    assert "not on path" in msg.lower()

    entry = next(c for c in checks._CHECKS if c[0] == "vale")
    assert entry[2] is False, "vale check must not fail the overall doctor run"


def test_check_vale_reports_presence(monkeypatch):
    monkeypatch.setattr(checks.shutil, "which", lambda name: "/usr/local/bin/vale")
    ok, msg = checks._check_vale(".")
    assert ok is True
    assert "/usr/local/bin/vale" in msg


def test_check_mcp_imports_rejects_an_mcp_release_without_the_decorators(monkeypatch):
    """A schema that builds says nothing about whether the server can start.

    `start_server` binds its handlers with `@server.list_tools()` and
    `@server.call_tool()`. mcp 2.0 removed both, so the server raises
    AttributeError at startup while the schema still generates fine — doctor
    reported "ready" for an install whose every tool was dead.
    """
    import mcp.server

    class _ServerWithoutDecorators:
        """Stands in for the 2.x Server, which registers via add_request_handler."""

        def add_request_handler(self, *args, **kwargs):  # pragma: no cover - shape only
            raise NotImplementedError

    monkeypatch.setattr(mcp.server, "Server", _ServerWithoutDecorators)

    ok, msg = checks._check_mcp_imports(".")
    assert ok is False
    assert "list_tools" in msg
    assert "mcp" in msg.lower()


def test_check_mcp_imports_passes_against_the_supported_line():
    ok, msg = checks._check_mcp_imports(".")
    assert ok is True
    assert "tools" in msg
