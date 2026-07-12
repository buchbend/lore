"""Folded CLI groups — old top-level verbs gone, merged verbs work.

Guards both directions. Structural assertions introspect the click
command tree rather than parsing `--help` text, so they don't depend on
Rich's terminal rendering.
"""

from __future__ import annotations

import json
from pathlib import Path

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


def test_journal_hidden_but_registered() -> None:
    """Parked feature: code stays, help listing doesn't."""
    journal = _root().commands["journal"]
    assert journal.hidden is True


def test_journal_still_invocable() -> None:
    result = runner.invoke(app, ["journal", "--help"])
    assert result.exit_code == 0


# ---- `lore attach remove` (was `lore detach`) ----

def _claude_md_with_section(tmp_path: Path) -> Path:
    target = tmp_path / "CLAUDE.md"
    target.write_text("# Repo\n\nIntro.\n\n## Lore\n\nManaged block.\n\n## Other\n\nKeep me.\n")
    return target


def test_attach_remove_strips_lore_section(tmp_path: Path) -> None:
    target = _claude_md_with_section(tmp_path)

    result = runner.invoke(app, ["attach", "remove", "--path", str(tmp_path)])

    assert result.exit_code == 0, result.output
    text = target.read_text()
    assert "## Lore" not in text
    assert "## Other" in text


def test_attach_remove_json_envelope(tmp_path: Path) -> None:
    _claude_md_with_section(tmp_path)

    result = runner.invoke(app, ["attach", "remove", "--path", str(tmp_path), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema"] == "lore.attach.remove/1"
    assert payload["data"]["removed"] is True


def test_attach_remove_is_noop_without_section(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("# Repo\n\nNo managed block.\n")

    result = runner.invoke(app, ["attach", "remove", "--path", str(tmp_path), "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["data"]["removed"] is False


# ---- `lore migrate` one-shot upgrade verbs ----

@pytest.mark.parametrize("verb", ["frontmatter", "slugs", "open-items"])
def test_migrate_exposes_upgrade_verb(verb: str) -> None:
    migrate = _root().commands["migrate"]
    assert verb in migrate.commands


def test_curator_backfill_slugs_entry_point_gone() -> None:
    """The one-shot slug rename moved to `lore migrate slugs`."""
    curator = _root().commands["curator"]
    assert "backfill-slugs" not in curator.commands


def test_curator_migrate_open_items_flag_gone() -> None:
    """The v1 -> v2 open-items rewrite moved to `lore migrate open-items`."""
    result = runner.invoke(app, ["curator", "--migrate-open-items"])
    assert result.exit_code != 0
