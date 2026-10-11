"""Folded CLI groups — old top-level verbs gone, merged verbs work.

Guards both directions. Structural assertions introspect the click
command tree rather than parsing `--help` text, so they don't depend on
Rich's terminal rendering.
"""

from __future__ import annotations

import pytest
import typer
from lore_cli.__main__ import app
from typer.testing import CliRunner

runner = CliRunner()

# Groups whose verbs now live under a parent group (or are deleted
# outright, in `completions`' case — Typer's `--install-completion`
# covers it).
RETIRED_GROUPS = ["attachments", "completions", "detach", "registry"]


def _root() -> typer.main.click.Group:
    return typer.main.get_command(app)


def test_root_offers_native_shell_completion() -> None:
    """The root app must opt into Typer's built-in completion machinery —
    that's what actually covers the retired `completions` group above.
    """
    param_names = {p.name for p in _root().params}
    assert {"install_completion", "show_completion"} <= param_names


@pytest.mark.parametrize("group", RETIRED_GROUPS)
def test_retired_group_not_registered(group: str) -> None:
    assert group not in _root().commands


@pytest.mark.parametrize("group", RETIRED_GROUPS)
def test_retired_group_invocation_fails(group: str) -> None:
    result = runner.invoke(app, [group, "--help"])
    assert result.exit_code != 0


# ---- `lore migrate` one-shot upgrade verbs ----

@pytest.mark.parametrize("verb", ["frontmatter"])
def test_migrate_exposes_upgrade_verb(verb: str) -> None:
    migrate = _root().commands["migrate"]
    assert verb in migrate.commands


def test_migrate_slugs_verb_is_gone() -> None:
    """The slug rename only ever renamed session notes, which no longer exist."""
    migrate = _root().commands["migrate"]
    assert "slugs" not in migrate.commands


@pytest.mark.parametrize("verb", ["open-items", "retire-session-notes"])
def test_migrate_session_note_verb_is_gone(verb: str) -> None:
    """Both backed session-note artifacts the compose pipeline stopped writing."""
    migrate = _root().commands["migrate"]
    assert verb not in migrate.commands


def test_curator_migrate_open_items_flag_gone() -> None:
    """The v1 -> v2 open-items rewrite moved to `lore migrate open-items`."""
    result = runner.invoke(app, ["curator", "--migrate-open-items"])
    assert result.exit_code != 0
