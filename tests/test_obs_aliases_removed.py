"""`lore log` / `lore runs` / `lore proc` / `lore news` are gone.

These four command groups were deprecated in favor of `lore trace` and
`lore status` (each carried a `_DEPRECATION_NOTICE` saying so); this test
locks in their removal.
"""

from __future__ import annotations

import typer.main
from lore_cli.__main__ import app
from typer.testing import CliRunner

runner = CliRunner()

_REMOVED = ("log", "runs", "proc", "news")


def test_removed_groups_are_unknown_commands() -> None:
    for name in _REMOVED:
        result = runner.invoke(app, [name, "--help"])
        assert result.exit_code != 0, f"`lore {name}` should be an unknown command"
        assert "No such command" in result.output


def test_removed_groups_are_not_mounted() -> None:
    click_group = typer.main.get_command(app)
    for name in _REMOVED:
        assert name not in click_group.commands, f"`{name}` is still mounted on the CLI"


def test_status_and_trace_still_mounted() -> None:
    click_group = typer.main.get_command(app)
    assert "status" in click_group.commands
    assert "trace" in click_group.commands
