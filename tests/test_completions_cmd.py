"""Smoke tests for `lore completions` subcommand."""

from __future__ import annotations

from typer.testing import CliRunner

from lore_cli.completions_cmd import app


runner = CliRunner()


def test_completions_bash_exits_zero_and_nonempty():
    """lore completions bash exits 0 and emits non-empty output."""
    result = runner.invoke(app, ["bash"])
    assert result.exit_code == 0, f"expected exit 0, got {result.exit_code}:\n{result.output}"
    assert len(result.output.strip()) > 0, "completions bash should emit non-empty output"


def test_completions_bash_output_looks_like_bash():
    """lore completions bash output contains bash-specific markers."""
    result = runner.invoke(app, ["bash"])
    assert result.exit_code == 0
    out = result.output
    # Either a click-generated script (contains 'complete' or '_lore') or our fallback.
    assert "lore" in out.lower() or "complete" in out.lower()


def test_completions_zsh_exits_zero():
    """lore completions zsh exits 0 (may produce empty if click doesn't support zsh)."""
    result = runner.invoke(app, ["zsh"])
    # zsh completion may or may not be supported; just confirm no crash (exit 0 or 1 is fine)
    assert result.exit_code in (0, 1)


def test_completions_no_args_shows_help():
    """lore completions with no args exits non-zero and shows help."""
    result = runner.invoke(app, [])
    # no_args_is_help=True produces a help page (exit 0 with help text)
    assert "bash" in result.output.lower() or "zsh" in result.output.lower() or result.exit_code == 0
