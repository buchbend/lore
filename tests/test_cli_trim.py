"""CLI trim (#469, #470): visible commands, hidden machine commands, deleted commands."""

from __future__ import annotations

import pytest
import typer
from lore_cli.__main__ import app
from typer.testing import CliRunner

runner = CliRunner()

VISIBLE = {"setup", "status", "config", "search", "read", "wiki", "update", "uninstall"}
HIDDEN = {
    "hook",
    "mcp",
    "workflow",
    "tier",
    "codemap",
    "trace",
    "transcripts",
    "style",
    "ingest",
    "inbox",
    "handover",
    "on",
    "off",
    "migrate",
}
DELETED = [
    "drill",
    "curator",
    "lint",
    "session",
    "project",
    "journal",
    "install",
    "init",
    "attach",
    "scopes",
    "doctor",
]


def _root():
    return typer.main.get_command(app)


def test_help_lists_exactly_the_visible_commands() -> None:
    root = _root()
    listed = {n for n, c in root.commands.items() if not c.hidden}
    assert listed == VISIBLE


@pytest.mark.parametrize("name", sorted(HIDDEN))
def test_machine_commands_hidden_but_registered(name: str) -> None:
    assert _root().commands[name].hidden is True


@pytest.mark.parametrize("name", DELETED)
def test_deleted_command_is_usage_error(name: str) -> None:
    assert name not in _root().commands
    assert runner.invoke(app, [name]).exit_code != 0


def test_wiki_holds_new_lint_curate() -> None:
    wiki = _root().commands["wiki"]
    assert {"new", "lint", "curate"} <= set(wiki.commands)


def test_migrate_has_no_flag_blocks() -> None:
    assert "flag-blocks" not in _root().commands["migrate"].commands


def test_no_journal_mcp_tool() -> None:
    from lore_mcp.server import _tool_schema

    assert "lore_journal_write" not in {t["name"] for t in _tool_schema()}
