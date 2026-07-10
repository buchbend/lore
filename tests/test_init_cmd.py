"""Tests for `lore init` — display-name onboarding prompt."""

from __future__ import annotations

from pathlib import Path

from lore_cli.init_cmd import app, init_vault
from lore_core.root_config import load_root_config
from typer.testing import CliRunner

runner = CliRunner()


def test_init_vault_noninteractive_leaves_display_name_unset(tmp_path: Path):
    init_vault(tmp_path)
    assert load_root_config(tmp_path).user.display_name == ""


def test_init_vault_display_name_arg_persists(tmp_path: Path):
    init_vault(tmp_path, display_name="Christof")
    assert load_root_config(tmp_path).user.display_name == "Christof"


def test_init_vault_already_configured_not_reprompted(tmp_path: Path, monkeypatch):
    """Re-running init (e.g. --force) must not clobber an existing name."""
    from lore_core.root_config import set_field

    set_field(tmp_path, "user.display_name", "Christof")
    monkeypatch.setattr("lore_cli.init_cmd._is_interactive", lambda: True)
    init_vault(tmp_path, force=True)
    assert load_root_config(tmp_path).user.display_name == "Christof"


def test_init_cli_interactive_prompts_and_persists(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("lore_cli.init_cmd._is_interactive", lambda: True)
    result = runner.invoke(app, ["--root", str(tmp_path)], input="Christof\n")
    assert result.exit_code == 0, result.output
    assert load_root_config(tmp_path).user.display_name == "Christof"


def test_init_cli_interactive_blank_leaves_unset(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("lore_cli.init_cmd._is_interactive", lambda: True)
    result = runner.invoke(app, ["--root", str(tmp_path)], input="\n")
    assert result.exit_code == 0, result.output
    assert load_root_config(tmp_path).user.display_name == ""


def test_init_cli_noninteractive_never_prompts(tmp_path: Path):
    """No --input, no isatty patch: CliRunner stdin is not a tty, so this
    must complete without blocking on a prompt."""
    result = runner.invoke(app, ["--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert load_root_config(tmp_path).user.display_name == ""


def test_init_cli_display_name_flag_noninteractive(tmp_path: Path):
    result = runner.invoke(app, ["--root", str(tmp_path), "--display-name", "Christof"])
    assert result.exit_code == 0, result.output
    assert load_root_config(tmp_path).user.display_name == "Christof"
