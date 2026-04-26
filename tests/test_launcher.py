"""Tests for the cross-integration launcher."""

from __future__ import annotations

from pathlib import Path

import pytest
from lore_cli import launcher
from lore_cli.launcher import (
    IntegrationConfig,
    build_invocation,
    list_integrations,
    load_integration,
)


def test_bundled_integrations_loadable():
    """Both bundled integrations (claude, opencode) should load and validate."""
    integrations = list_integrations()
    assert "claude" in integrations
    assert "opencode" in integrations
    for name in ("claude", "opencode"):
        integration = load_integration(name)
        assert integration is not None
        ok, msg = integration.is_valid()
        assert ok, f"{name} invalid: {msg}"


def test_unknown_integration():
    assert load_integration("nonexistent-integration-name") is None


def test_user_override_dir(tmp_path, monkeypatch):
    """LORE_INTEGRATIONS_DIR should take precedence over the bundled dir."""
    user_dir = tmp_path / "integrations"
    user_dir.mkdir()
    (user_dir / "claude.toml").write_text(
        'binary = "my-custom-claude"\n'
        'context_format = "stdin"\n'
        'context_flag = ""\n'
        "extra_args = []\n"
    )
    monkeypatch.setenv("LORE_INTEGRATIONS_DIR", str(user_dir))
    integration = load_integration("claude")
    assert integration is not None
    assert integration.binary == "my-custom-claude"
    assert integration.context_format == "stdin"


def test_build_invocation_flag_format():
    integration = IntegrationConfig(
        name="claude",
        binary="claude",
        context_format="flag",
        context_flag="--append-system-prompt",
        extra_args=[],
        source_path=Path("/tmp/claude.toml"),
    )
    argv, stdin = build_invocation(integration, "CTX", user_message="hello")
    assert argv == ["claude", "--append-system-prompt", "CTX", "hello"]
    assert stdin is None


def test_build_invocation_stdin_format():
    integration = IntegrationConfig(
        name="x",
        binary="x",
        context_format="stdin",
        context_flag="",
        extra_args=["--non-interactive"],
        source_path=Path("/tmp/x.toml"),
    )
    argv, stdin = build_invocation(integration, "CTX", user_message=None)
    assert argv == ["x", "--non-interactive"]
    assert stdin == "CTX"


def test_build_invocation_prepend_combines_into_message():
    integration = IntegrationConfig(
        name="oc",
        binary="oc",
        context_format="prepend",
        context_flag="",
        extra_args=[],
        source_path=Path("/tmp/oc.toml"),
    )
    argv, stdin = build_invocation(integration, "CTX", user_message="please help")
    assert argv == ["oc", "CTX\n\nplease help"]
    assert stdin is None


def test_build_invocation_append_combines_into_message():
    integration = IntegrationConfig(
        name="oc",
        binary="oc",
        context_format="append",
        context_flag="",
        extra_args=[],
        source_path=Path("/tmp/oc.toml"),
    )
    argv, stdin = build_invocation(integration, "CTX", user_message="please help")
    assert argv == ["oc", "please help\n\nCTX"]
    assert stdin is None


def test_validation_flag_without_context_flag():
    integration = IntegrationConfig(
        name="x",
        binary="x",
        context_format="flag",
        context_flag="",
        extra_args=[],
        source_path=Path("/tmp/x.toml"),
    )
    ok, msg = integration.is_valid()
    assert not ok
    assert "context_flag" in msg


def test_launch_dry_run_does_not_exec(capsys):
    """--dry-run prints the would-be invocation and returns 0."""
    rc = launcher.launch("claude", context_text="ctx", dry_run=True)
    assert rc == 0
    err = capsys.readouterr().err
    assert "would exec" in err
    assert "claude" in err


def test_launch_unknown_integration_returns_2(capsys):
    rc = launcher.launch("definitely-not-an-integration", context_text="x", dry_run=True)
    assert rc == 2
    err = capsys.readouterr().err
    assert "no integration" in err
