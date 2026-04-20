"""Tests for `lore_core.install.claude` — argv assertions for the
`claude plugin install` subprocess (mocked) + the bootstrap-when-lore-
not-on-PATH self-install case."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from lore_core.install import _helpers, claude
from lore_core.install.base import InstallContext


def test_plan_emits_claude_plugin_install_command():
    actions = claude.plan(InstallContext())
    run_actions = [a for a in actions if a.kind == "run"]
    cmds = [a.payload.get("argv") for a in run_actions]
    assert ["claude", "plugin", "install", "lore@lore"] in cmds


def test_plan_registers_marketplace_before_plugin_install():
    """`claude plugin install lore@lore` requires the `lore` marketplace
    to be registered first (real-machine test, 2026-04-18 caught this).
    The marketplace-add action must precede the plugin-install action.
    """
    actions = claude.plan(InstallContext())
    run_argvs = [a.payload.get("argv") for a in actions if a.kind == "run"]
    add_idx = next(
        i
        for i, argv in enumerate(run_argvs)
        if argv == ["claude", "plugin", "marketplace", "add", "buchbend/lore"]
    )
    install_idx = next(
        i
        for i, argv in enumerate(run_argvs)
        if argv == ["claude", "plugin", "install", "lore@lore"]
    )
    assert add_idx < install_idx, (
        "marketplace add must run before plugin install"
    )


def test_marketplace_add_action_is_continue_on_failure():
    """Re-adding an already-registered marketplace produces a benign
    error; on_failure=continue lets the install proceed to the plugin
    step regardless."""
    actions = claude.plan(InstallContext())
    add_actions = [
        a
        for a in actions
        if a.kind == "run"
        and a.payload.get("argv") == [
            "claude",
            "plugin",
            "marketplace",
            "add",
            "buchbend/lore",
        ]
    ]
    assert len(add_actions) == 1
    assert add_actions[0].on_failure == "continue"


def test_plan_emits_lore_on_path_check_at_end():
    actions = claude.plan(InstallContext())
    check_actions = [a for a in actions if a.kind == "check"]
    assert any(
        a.payload.get("check") == "lore_on_path" for a in check_actions
    )


def test_plan_bootstraps_self_install_when_lore_missing(monkeypatch):
    """If `lore` is not on PATH, plan() prepends an install_self_via run
    action (pipx → uv → pip cascade)."""
    monkeypatch.setattr(
        _helpers.shutil,
        "which",
        lambda b: "/fake/pipx" if b == "pipx" else None,  # lore returns None
    )
    actions = claude.plan(InstallContext())
    pipx_runs = [
        a
        for a in actions
        if a.kind == "run" and (a.payload.get("argv") or [""])[0] == "pipx"
    ]
    assert len(pipx_runs) == 1, "self-install bootstrap must fire when lore missing"


def test_plan_skips_bootstrap_when_lore_on_path(monkeypatch):
    """If `lore` is already on PATH, no self-install action."""
    real_which = shutil.which

    def fake_which(b):
        if b == "lore":
            return "/fake/lore"
        return real_which(b)

    monkeypatch.setattr(_helpers.shutil, "which", fake_which)
    actions = claude.plan(InstallContext())
    pipx_runs = [
        a
        for a in actions
        if a.kind == "run" and (a.payload.get("argv") or [""])[0] == "pipx"
    ]
    assert pipx_runs == []


def test_uninstall_plan_emits_claude_plugin_uninstall():
    actions = claude.uninstall_plan(InstallContext())
    cmds = [a.payload.get("argv") for a in actions if a.kind == "run"]
    assert ["claude", "plugin", "uninstall", "lore@lore"] in cmds


@pytest.mark.skipif(
    shutil.which("claude") is None, reason="claude CLI not on PATH"
)
def test_claude_binary_help_smoke():
    """Integration smoke: `claude plugin --help` succeeds. Skipped when
    `claude` isn't installed locally."""
    import subprocess

    result = subprocess.run(
        ["claude", "plugin", "--help"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0 or "plugin" in (result.stdout + result.stderr).lower()


# ---------------------------------------------------------------------------
# T17: plugin.json capture-hook wiring
# ---------------------------------------------------------------------------

def _plugin_json_path() -> Path:
    """Locate .claude-plugin/plugin.json relative to the repo root."""
    # Walk up from this test file to find the repo root (contains .claude-plugin/)
    candidate = Path(__file__).resolve().parent
    for _ in range(10):
        plugin_json = candidate / ".claude-plugin" / "plugin.json"
        if plugin_json.exists():
            return plugin_json
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    raise FileNotFoundError(".claude-plugin/plugin.json not found in repo tree")


def test_plugin_json_parses_cleanly():
    """plugin.json must be valid JSON and declare the expected top-level keys."""
    path = _plugin_json_path()
    data = json.loads(path.read_text())
    assert "hooks" in data, "plugin.json must have a 'hooks' key"
    assert "mcpServers" in data, "plugin.json must have an 'mcpServers' key"


def test_install_writes_capture_hooks():
    """plugin.json must wire SessionEnd, PreCompact, and SessionStart to
    `lore hook capture --event <name>` so passive transcript capture fires
    on every Claude Code hook event.

    The installer runs `claude plugin install lore@lore` which reads the
    manifest directly — this test asserts the manifest already contains
    the three required entries.
    """
    path = _plugin_json_path()
    data = json.loads(path.read_text())
    hooks = data["hooks"]

    # Collect all command strings across all events
    def all_commands(event_key: str) -> list[str]:
        return [
            h["command"]
            for grp in hooks.get(event_key, [])
            for h in grp["hooks"]
        ]

    assert "lore hook capture --event session-end" in all_commands("SessionEnd"), (
        "SessionEnd must call `lore hook capture --event session-end`"
    )
    assert "lore hook capture --event pre-compact" in all_commands("PreCompact"), (
        "PreCompact must call `lore hook capture --event pre-compact`"
    )
    assert "lore hook capture --event session-start" in all_commands("SessionStart"), (
        "SessionStart must call `lore hook capture --event session-start`"
    )
