"""Tests for the cross-host launcher."""

from __future__ import annotations

from pathlib import Path

import pytest
from lore_cli import launcher
from lore_cli.launcher import HostConfig, build_invocation, list_hosts, load_host


def test_bundled_hosts_loadable():
    """Both bundled hosts (claude, opencode) should load and validate."""
    hosts = list_hosts()
    assert "claude" in hosts
    assert "opencode" in hosts
    for name in ("claude", "opencode"):
        host = load_host(name)
        assert host is not None
        ok, msg = host.is_valid()
        assert ok, f"{name} invalid: {msg}"


def test_unknown_host():
    assert load_host("nonexistent-host-name") is None


def test_user_override_dir(tmp_path, monkeypatch):
    """LORE_HOSTS_DIR should take precedence over the bundled dir."""
    user_dir = tmp_path / "hosts"
    user_dir.mkdir()
    (user_dir / "claude.toml").write_text(
        'binary = "my-custom-claude"\n'
        'context_format = "stdin"\n'
        'context_flag = ""\n'
        "extra_args = []\n"
    )
    monkeypatch.setenv("LORE_HOSTS_DIR", str(user_dir))
    host = load_host("claude")
    assert host is not None
    assert host.binary == "my-custom-claude"
    assert host.context_format == "stdin"


def test_build_invocation_flag_format():
    host = HostConfig(
        name="claude",
        binary="claude",
        context_format="flag",
        context_flag="--append-system-prompt",
        extra_args=[],
        source_path=Path("/tmp/claude.toml"),
    )
    argv, stdin = build_invocation(host, "CTX", user_message="hello")
    assert argv == ["claude", "--append-system-prompt", "CTX", "hello"]
    assert stdin is None


def test_build_invocation_stdin_format():
    host = HostConfig(
        name="x",
        binary="x",
        context_format="stdin",
        context_flag="",
        extra_args=["--non-interactive"],
        source_path=Path("/tmp/x.toml"),
    )
    argv, stdin = build_invocation(host, "CTX", user_message=None)
    assert argv == ["x", "--non-interactive"]
    assert stdin == "CTX"


def test_build_invocation_prepend_combines_into_message():
    host = HostConfig(
        name="oc",
        binary="oc",
        context_format="prepend",
        context_flag="",
        extra_args=[],
        source_path=Path("/tmp/oc.toml"),
    )
    argv, stdin = build_invocation(host, "CTX", user_message="please help")
    assert argv == ["oc", "CTX\n\nplease help"]
    assert stdin is None


def test_build_invocation_append_combines_into_message():
    host = HostConfig(
        name="oc",
        binary="oc",
        context_format="append",
        context_flag="",
        extra_args=[],
        source_path=Path("/tmp/oc.toml"),
    )
    argv, stdin = build_invocation(host, "CTX", user_message="please help")
    assert argv == ["oc", "please help\n\nCTX"]
    assert stdin is None


def test_validation_flag_without_context_flag():
    host = HostConfig(
        name="x",
        binary="x",
        context_format="flag",
        context_flag="",
        extra_args=[],
        source_path=Path("/tmp/x.toml"),
    )
    ok, msg = host.is_valid()
    assert not ok
    assert "context_flag" in msg


def test_launch_dry_run_does_not_exec(capsys):
    """--dry-run prints the would-be invocation and returns 0."""
    rc = launcher.launch("claude", context_text="ctx", dry_run=True)
    assert rc == 0
    err = capsys.readouterr().err
    assert "would exec" in err
    assert "claude" in err


def test_launch_unknown_host_returns_2(capsys):
    rc = launcher.launch("definitely-not-a-host", context_text="x", dry_run=True)
    assert rc == 2
    err = capsys.readouterr().err
    assert "no host" in err
