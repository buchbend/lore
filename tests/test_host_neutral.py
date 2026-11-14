# ruff: noqa: F811
"""Host-neutral hook commands and instruction files (#611, PRD 0022)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from lore_cli import hooks
from lore_cli.__main__ import app
from lore_cli.hooks import _context_log, hook_app
from lore_core.tiers import resolve_tier
from test_hooks_capture import _make_attached_project
from test_setup_cmd import agents  # noqa: F401,F811
from typer.testing import CliRunner

runner = CliRunner()
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("LORE_SUPPRESS_CAPTURE", raising=False)
    monkeypatch.delenv("LORE_HOST", raising=False)
    monkeypatch.delenv("LORE_SESSION_ID", raising=False)


def test_capture_default_adapter_is_claude_code(tmp_path, monkeypatch):
    project = _make_attached_project(tmp_path)
    seen: list[str] = []
    monkeypatch.setattr(
        hooks, "get_adapter", lambda n: seen.append(n) or (_ for _ in ()).throw(RuntimeError)
    )
    monkeypatch.setenv("LORE_ROOT", str(project))
    runner.invoke(hook_app, ["capture", "--event", "session-end", "--cwd", str(project)])
    assert seen == ["claude-code"]


def test_capture_uses_lore_host_adapter(tmp_path, monkeypatch):
    project = _make_attached_project(tmp_path)
    seen: list[str] = []
    monkeypatch.setattr(
        hooks, "get_adapter", lambda n: seen.append(n) or (_ for _ in ()).throw(RuntimeError)
    )
    monkeypatch.setenv("LORE_ROOT", str(project))
    monkeypatch.setenv("LORE_HOST", "opencode")
    runner.invoke(hook_app, ["capture", "--event", "session-end", "--cwd", str(project)])
    assert seen == ["opencode"]


def test_capture_unknown_lore_host_exits_0_and_logs_crash(tmp_path, monkeypatch):
    project = _make_attached_project(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(project))
    monkeypatch.setenv("LORE_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("LORE_HOST", "no-such-host")
    monkeypatch.setattr("lore_cli._crash_log._is_dev_invocation", lambda: False)
    result = runner.invoke(hook_app, ["capture", "--event", "session-end", "--cwd", str(project)])
    assert result.exit_code == 0, result.output
    logs = list((tmp_path / "cache" / "crashes").glob("*.log"))
    assert len(logs) == 1 and "no-such-host" in logs[0].read_text()


def test_context_log_reads_lore_session_id_cache(tmp_path, monkeypatch):
    sessions = tmp_path / "cache" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "ses_abc.md").write_text("opencode session log\n")
    monkeypatch.setenv("LORE_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("LORE_SESSION_ID", "ses_abc")
    monkeypatch.setattr("lore_cli.hooks._claude_code_pid", lambda: None)
    assert "opencode session log" in _context_log()


def test_tier_resolve_opencode_host(tmp_path, monkeypatch):
    (tmp_path / ".lore").mkdir()
    from lore_core.tiers import detect_host

    for var in ("CLAUDECODE", "CLAUDE_PROJECT_DIR", "CLAUDE_SESSION_ID", "CLAUDE_CODE_EXECPATH"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LORE_HOST", "opencode")
    assert detect_host() == "opencode"
    assert resolve_tier("mid", lore_root=tmp_path)


def test_repo_claude_md_only_imports_agents_md():
    assert (REPO / "CLAUDE.md").read_text().strip() == "@AGENTS.md"
    assert "## Releasing" in (REPO / "AGENTS.md").read_text()


def test_setup_writes_agents_md_and_claude_md_import(tmp_path, agents):
    vault = tmp_path / "lore"
    result = runner.invoke(app, ["setup", "--yes", "--vault", str(vault)])
    assert result.exit_code == 0, result.output
    assert (vault / "AGENTS.md").read_text().strip()
    assert (vault / "CLAUDE.md").read_text().strip() == "@AGENTS.md"


def test_setup_leaves_foreign_claude_md_unchanged_beside_agents_md(tmp_path, agents):
    vault = tmp_path / "lore"
    vault.mkdir()
    (vault / "CLAUDE.md").write_text("my own rules\n")
    (vault / "AGENTS.md").write_text("agent rules\n")
    runner.invoke(app, ["setup", "--yes", "--vault", str(vault)])
    assert (vault / "CLAUDE.md").read_text() == "my own rules\n"


def test_skills_hold_no_claude_code_tool_forms():
    for f in (REPO / "skills").rglob("*.md"):
        assert "mcp__lore__" not in f.read_text(), f
    assert "subagent_type" not in (REPO / "skills" / "orient" / "SKILL.md").read_text()
    assert "subagent_type" not in (REPO / "skills" / "TIER-DELEGATION.md").read_text()


def test_session_start_cache_survives_gc(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("LORE_SESSION_ID", "ses_abc")
    monkeypatch.setattr("lore_cli.hooks._claude_code_pid", lambda: None)
    hooks._emit("SessionStart", "banner-body", plain=False)
    assert "banner-body" in _context_log()


def test_unsafe_session_id_falls_back_to_pid_path(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("LORE_SESSION_ID", "../x")
    monkeypatch.setattr("lore_cli.hooks._claude_code_pid", lambda: os.getpid())
    hooks._emit("SessionStart", "banner-body", plain=False)
    assert not (tmp_path / "cache" / "x.md").exists()
    assert (tmp_path / "cache" / "sessions" / f"{os.getpid()}.md").exists()


def test_setup_writes_claude_md_beside_existing_agents_md(tmp_path, agents):
    vault = tmp_path / "lore"
    vault.mkdir()
    (vault / "AGENTS.md").write_text("mine\n")
    runner.invoke(app, ["setup", "--yes", "--vault", str(vault)])
    assert (vault / "AGENTS.md").read_text() == "mine\n"
    assert (vault / "CLAUDE.md").read_text().strip() == "@AGENTS.md"
