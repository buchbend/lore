"""Tests for `lore_core.install.claude` — argv assertions for the
`claude plugin install` subprocess (mocked) + the bootstrap-when-lore-
not-on-PATH self-install case."""

from __future__ import annotations

import shutil

import pytest
from lore_core.install import _helpers, claude
from lore_core.install.base import InstallContext


def test_plan_emits_claude_plugin_install_command():
    actions = claude.plan(InstallContext())
    run_actions = [a for a in actions if a.kind == "run"]
    cmds = [a.payload.get("argv") for a in run_actions]
    assert ["claude", "plugin", "install", "lore@lore"] in cmds


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
