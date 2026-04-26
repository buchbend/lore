"""`lore wiki ...` group tests — Phase 8 alias for the legacy `lore new-wiki`.

The canonical form is now ``lore wiki new <name>`` (matches the
namespaced ``lore surface init/add/commit`` shape). The legacy
``lore new-wiki <name>`` keeps working and forwards to the same
``scaffold_wiki()`` implementation; both paths are pinned here so a
future refactor can't silently break either.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from lore_cli.__main__ import app

runner = CliRunner()


@pytest.fixture()
def lore_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    (tmp_path / "wiki").mkdir()
    return tmp_path


def test_lore_wiki_new_invokes_scaffold(lore_root: Path) -> None:
    """`lore wiki new <name>` calls scaffold_wiki via the new namespaced form."""
    with patch("lore_cli.new_wiki_cmd.scaffold_wiki") as mock_scaffold:
        result = runner.invoke(app, ["wiki", "new", "test-wiki", "--mode", "personal"])
    assert result.exit_code == 0, result.output
    mock_scaffold.assert_called_once()
    kwargs = mock_scaffold.call_args.kwargs
    assert mock_scaffold.call_args.args == ("test-wiki",)
    assert kwargs["mode"] == "personal"


def test_lore_new_wiki_legacy_alias_still_works(lore_root: Path) -> None:
    """`lore new-wiki <name>` (legacy) reaches the same scaffolder."""
    with patch("lore_cli.new_wiki_cmd.scaffold_wiki") as mock_scaffold:
        result = runner.invoke(app, ["new-wiki", "test-wiki"])
    assert result.exit_code == 0, result.output
    mock_scaffold.assert_called_once()
    assert mock_scaffold.call_args.args == ("test-wiki",)


def test_lore_new_wiki_legacy_emits_migration_hint(lore_root: Path) -> None:
    """The legacy form prints a one-line stderr hint pointing at
    `lore wiki new` so users gradually migrate."""
    with patch("lore_cli.new_wiki_cmd.scaffold_wiki"):
        result = runner.invoke(app, ["new-wiki", "test-wiki"])
    assert result.exit_code == 0
    # CliRunner mixes stdout + stderr by default; Rich may have inserted
    # a soft line-break in the middle of the hint, so normalise whitespace
    # before substring-matching.
    flat = " ".join(result.output.split())
    assert "lore wiki new" in flat
    assert "canonical form" in flat


def test_lore_wiki_new_does_not_emit_migration_hint(lore_root: Path) -> None:
    """The canonical form should NOT nag — only the legacy alias does."""
    with patch("lore_cli.new_wiki_cmd.scaffold_wiki"):
        result = runner.invoke(app, ["wiki", "new", "test-wiki"])
    assert result.exit_code == 0
    assert "canonical form is now" not in result.output
