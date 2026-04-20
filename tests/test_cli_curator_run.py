"""Tests for `lore curator run` CLI command."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lore_cli.__main__ import app

runner = CliRunner(mix_stderr=False)


@dataclass
class FakeCuratorAResult:
    transcripts_considered: int = 0
    noteworthy_count: int = 0
    new_notes: list = field(default_factory=list)
    merged_notes: list = field(default_factory=list)
    skipped_reasons: dict = field(default_factory=dict)
    duration_seconds: float = 0.0


def _make_fake_run(store: list, result=None):
    """Return a fake run_curator_a that records kwargs and returns result."""
    if result is None:
        result = FakeCuratorAResult()

    def fake_run(**kwargs):
        store.append(kwargs)
        return result

    return fake_run


def test_curator_run_invokes_pipeline(tmp_path, monkeypatch):
    """curator run calls run_curator_a."""
    lore_root = tmp_path / "lore_root"
    lore_root.mkdir()
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    calls = []
    monkeypatch.setattr(
        "lore_curator.curator_a.run_curator_a",
        _make_fake_run(calls),
    )

    result = runner.invoke(app, ["curator", "run"])
    assert result.exit_code == 0, result.output
    assert len(calls) == 1


def test_curator_run_dry_run_flag_propagates(tmp_path, monkeypatch):
    """--dry-run passes dry_run=True to run_curator_a."""
    lore_root = tmp_path / "lore_root"
    lore_root.mkdir()
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    calls = []
    monkeypatch.setattr(
        "lore_curator.curator_a.run_curator_a",
        _make_fake_run(calls),
    )

    result = runner.invoke(app, ["curator", "run", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert calls[0]["dry_run"] is True


def test_curator_run_reports_summary_to_stdout(tmp_path, monkeypatch):
    """Summary includes transcripts_considered and noteworthy_count."""
    lore_root = tmp_path / "lore_root"
    lore_root.mkdir()
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    canned_result = FakeCuratorAResult(
        transcripts_considered=5,
        noteworthy_count=3,
        new_notes=[Path("a.md"), Path("b.md"), Path("c.md")],
        merged_notes=[],
        skipped_reasons={"not_noteworthy": 2},
        duration_seconds=0.42,
    )
    calls = []
    monkeypatch.setattr(
        "lore_curator.curator_a.run_curator_a",
        _make_fake_run(calls, canned_result),
    )

    result = runner.invoke(app, ["curator", "run"])
    assert result.exit_code == 0, result.output
    assert "5" in result.output
    assert "3" in result.output


def test_curator_run_missing_anthropic_key_warns(tmp_path, monkeypatch):
    """Without ANTHROPIC_API_KEY the command runs without crashing and hints."""
    lore_root = tmp_path / "lore_root"
    lore_root.mkdir()
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    calls = []
    monkeypatch.setattr(
        "lore_curator.curator_a.run_curator_a",
        _make_fake_run(calls),
    )

    result = runner.invoke(app, ["curator", "run"])
    assert result.exit_code == 0, result.output
    # Should mention no key / anthropic / api_key in output
    combined = (result.output or "").lower()
    assert (
        "anthropic" in combined
        or "api_key" in combined
        or "key" in combined
    )
