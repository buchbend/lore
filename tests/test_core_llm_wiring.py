"""Acceptance tests for T6: make_llm_client wired into cmd_session_curator_run.

Each test drives `lore curator run --dry-run` through CliRunner and asserts
that the correct backend label (or skip warning) appears in the output.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lore_cli.__main__ import app

runner = CliRunner(mix_stderr=False)


# ---------------------------------------------------------------------------
# Minimal fake curator result so run_curator_a doesn't fail
# ---------------------------------------------------------------------------


@dataclass
class _FakeCuratorAResult:
    transcripts_considered: int = 0
    noteworthy_count: int = 0
    new_notes: list = field(default_factory=list)
    merged_notes: list = field(default_factory=list)
    skipped_reasons: dict = field(default_factory=dict)
    duration_seconds: float = 0.0


def _fake_run_a(**kwargs):
    return _FakeCuratorAResult()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_core_wires_subprocess_backend_when_claude_on_path(tmp_path, monkeypatch):
    """With `claude` on PATH, curator run announces the subscription backend."""
    lore_root = tmp_path / "lore_root"
    lore_root.mkdir()
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LORE_LLM_BACKEND", raising=False)

    # Make shutil.which("claude") return a plausible path
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/claude" if name == "claude" else None)

    monkeypatch.setattr("lore_curator.curator_a.run_curator_a", _fake_run_a)

    result = runner.invoke(app, ["curator", "run", "--dry-run"])
    assert result.exit_code == 0, result.output + (result.stderr or "")
    assert "Curator backend: Claude Code subscription (claude -p)" in result.output


def test_core_wires_sdk_backend_when_only_api_key_set(tmp_path, monkeypatch):
    """No `claude` binary, but ANTHROPIC_API_KEY set → SDK backend announced."""
    lore_root = tmp_path / "lore_root"
    lore_root.mkdir()
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-x")
    monkeypatch.delenv("LORE_LLM_BACKEND", raising=False)

    # No claude binary on PATH
    monkeypatch.setattr("shutil.which", lambda name: None)

    # Stub anthropic.Anthropic so SDKClient doesn't need real SDK init
    import types
    import sys

    fake_anthropic_mod = types.ModuleType("anthropic")

    class _FakeMessages:
        def create(self, **kwargs):
            raise RuntimeError("should not be called in dry-run")

    class _FakeAnthropic:
        def __init__(self, **kwargs):
            self.messages = _FakeMessages()

    fake_anthropic_mod.Anthropic = _FakeAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic_mod)

    monkeypatch.setattr("lore_curator.curator_a.run_curator_a", _fake_run_a)

    result = runner.invoke(app, ["curator", "run", "--dry-run"])
    assert result.exit_code == 0, result.output + (result.stderr or "")
    assert "Curator backend: Anthropic API (anthropic SDK)" in result.output


def test_core_prints_skip_warning_when_nothing_available(tmp_path, monkeypatch):
    """No `claude`, no api key → yellow warning printed to stderr."""
    lore_root = tmp_path / "lore_root"
    lore_root.mkdir()
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LORE_LLM_BACKEND", raising=False)

    # No claude binary on PATH
    monkeypatch.setattr("shutil.which", lambda name: None)

    monkeypatch.setattr("lore_curator.curator_a.run_curator_a", _fake_run_a)

    result = runner.invoke(app, ["curator", "run", "--dry-run"])
    assert result.exit_code == 0, result.output + (result.stderr or "")
    # Warning goes to stderr; Rich may wrap long lines, so check a stable fragment.
    combined_err = result.stderr or ""
    assert "Curator will skip AI classification" in combined_err
    assert "ANTHROPIC_API_KEY" in combined_err
