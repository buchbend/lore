"""Tests for `lore curator run` CLI command."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from lore_cli.__main__ import app
from typer.testing import CliRunner

runner = CliRunner()


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
        "lore_curator.session_curator.run_curator_a",
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
        "lore_curator.session_curator.run_curator_a",
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
        "lore_curator.session_curator.run_curator_a",
        _make_fake_run(calls, canned_result),
    )

    result = runner.invoke(app, ["curator", "run"])
    assert result.exit_code == 0, result.output
    assert "5" in result.output
    assert "3" in result.output


def test_curator_run_missing_anthropic_key_warns(tmp_path, monkeypatch):
    """Without ANTHROPIC_API_KEY (and no claude binary), the command runs and warns."""
    lore_root = tmp_path / "lore_root"
    lore_root.mkdir()
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LORE_LLM_BACKEND", raising=False)
    # Ensure no subprocess backend is picked up either
    monkeypatch.setattr("shutil.which", lambda name: None)

    calls = []
    monkeypatch.setattr(
        "lore_curator.session_curator.run_curator_a",
        _make_fake_run(calls),
    )

    result = runner.invoke(app, ["curator", "run"])
    assert result.exit_code == 0, result.output
    # Warning now goes to stderr via err_console; check it mentions classification skip
    combined = ((result.output or "") + (result.stderr or "")).lower()
    assert (
        "anthropic" in combined or "api_key" in combined or "key" in combined or "skip" in combined
    )


# ---------------------------------------------------------------------------
# Kill switch: `curator run` no longer accepts --abstract (Curator B)
# ---------------------------------------------------------------------------


def test_curator_run_default_does_not_invoke_curator_b(tmp_path, monkeypatch):
    """`curator run` (no flags) never touches Curator B."""
    lore_root = tmp_path / "lore_root"
    lore_root.mkdir()
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    calls_a = []
    calls_b = []
    monkeypatch.setattr(
        "lore_curator.session_curator.run_curator_a",
        _make_fake_run(calls_a),
    )
    monkeypatch.setattr(
        "lore_curator.daily_curator.run_curator_b",
        lambda **kwargs: calls_b.append(kwargs),
    )

    result = runner.invoke(app, ["curator", "run"])
    assert result.exit_code == 0, result.output
    assert len(calls_a) == 1
    assert len(calls_b) == 0


def test_curator_run_rejects_abstract_flag(tmp_path, monkeypatch):
    """`--abstract` is a severed entry point — the CLI no longer accepts it."""
    lore_root = tmp_path / "lore_root"
    lore_root.mkdir()
    monkeypatch.setenv("LORE_ROOT", str(lore_root))

    result = runner.invoke(app, ["curator", "run", "--abstract"])
    assert result.exit_code != 0, result.output
    assert "abstract" in result.output.lower()


# ---------------------------------------------------------------------------
# Tests for --trace-llm flag
# ---------------------------------------------------------------------------


def test_curator_run_trace_llm_flag_propagates(tmp_path, monkeypatch):
    """--trace-llm passes trace_llm=True to run_curator_a."""
    lore_root = tmp_path / "lore_root"
    lore_root.mkdir()
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    calls = []
    monkeypatch.setattr(
        "lore_curator.session_curator.run_curator_a",
        _make_fake_run(calls),
    )

    result = runner.invoke(app, ["curator", "run", "--trace-llm"])
    assert result.exit_code == 0, result.output
    assert calls[0]["trace_llm"] is True


def test_curator_run_env_var_enables_trace(tmp_path, monkeypatch):
    """LORE_TRACE_LLM=1 enables trace_llm=True in run_curator_a."""
    lore_root = tmp_path / "lore_root"
    lore_root.mkdir()
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    monkeypatch.setenv("LORE_TRACE_LLM", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    calls = []
    monkeypatch.setattr(
        "lore_curator.session_curator.run_curator_a",
        _make_fake_run(calls),
    )

    result = runner.invoke(app, ["curator", "run"])
    assert result.exit_code == 0, result.output
    assert calls[0]["trace_llm"] is True


def test_curator_run_trace_llm_and_dry_run_combine(tmp_path, monkeypatch):
    """--trace-llm and --dry-run work together."""
    lore_root = tmp_path / "lore_root"
    lore_root.mkdir()
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    calls = []
    monkeypatch.setattr(
        "lore_curator.session_curator.run_curator_a",
        _make_fake_run(calls),
    )

    result = runner.invoke(app, ["curator", "run", "--dry-run", "--trace-llm"])
    assert result.exit_code == 0, result.output
    assert calls[0]["dry_run"] is True
    assert calls[0]["trace_llm"] is True
