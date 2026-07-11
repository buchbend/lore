"""`lore wiki ...` group tests."""
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
    """`lore wiki new <name>` calls scaffold_wiki."""
    with patch("lore_cli.wiki_cmd.scaffold_wiki") as mock_scaffold:
        result = runner.invoke(app, ["wiki", "new", "test-wiki", "--mode", "personal"])
    assert result.exit_code == 0, result.output
    mock_scaffold.assert_called_once()
    kwargs = mock_scaffold.call_args.kwargs
    assert mock_scaffold.call_args.args == ("test-wiki",)
    assert kwargs["mode"] == "personal"


def test_lore_new_wiki_legacy_form_is_gone(lore_root: Path) -> None:
    """`lore new-wiki <name>` no longer exists; only `lore wiki new` remains."""
    result = runner.invoke(app, ["new-wiki", "test-wiki"])
    assert result.exit_code != 0


def test_scaffold_wiki_writes_commented_scopes_yml(lore_root: Path) -> None:
    """A new wiki gets a `_scopes.yml` seeded with a commented example — a
    starting point that parses to an empty tree (no phantom scopes)."""
    from lore_cli.wiki_cmd import scaffold_wiki
    from lore_core.scopes import load_scopes_yml

    target = scaffold_wiki("mywiki", mode="personal")
    scopes = target / "_scopes.yml"
    assert scopes.exists()
    assert "scopes:" in scopes.read_text()  # example present (commented)
    assert load_scopes_yml(target) == {}  # commented-only → empty tree
