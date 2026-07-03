"""`lore quarantine ...` CLI review flow — list / show / clear / kill."""

from __future__ import annotations

from pathlib import Path

import pytest
from lore_cli.__main__ import app
from lore_core import quarantine
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture()
def lore_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    (tmp_path / "wiki").mkdir()
    return tmp_path


def _seed(lore_root: Path, **overrides) -> quarantine.QuarantineEntry:
    kwargs = {
        "lore_root": lore_root,
        "category": "secret",
        "note_path": "sessions/2026/07/03-demo.md",
        "from_turn": 10,
        "to_turn": 20,
        "composed_text": "withheld chapter body",
    }
    kwargs.update(overrides)
    return quarantine.add_entry(**kwargs)


class TestList:
    def test_list_empty(self, lore_root: Path):
        result = runner.invoke(app, ["quarantine", "list"])
        assert result.exit_code == 0, result.output

    def test_list_shows_entries(self, lore_root: Path):
        e = _seed(lore_root, category="email")
        result = runner.invoke(app, ["quarantine", "list"])
        assert result.exit_code == 0, result.output
        assert e.id in result.output
        assert "email" in result.output

    def test_list_does_not_dump_full_body(self, lore_root: Path):
        # `list` is a terse index; the full (possibly secret) body is only
        # revealed by `show`.
        _seed(lore_root, composed_text="THE-SECRET-BODY-MARKER")
        result = runner.invoke(app, ["quarantine", "list"])
        assert "THE-SECRET-BODY-MARKER" not in result.output


class TestShow:
    def test_show_reveals_body(self, lore_root: Path):
        e = _seed(lore_root, composed_text="THE-SECRET-BODY-MARKER")
        result = runner.invoke(app, ["quarantine", "show", e.id])
        assert result.exit_code == 0, result.output
        assert "THE-SECRET-BODY-MARKER" in result.output

    def test_show_missing_errors(self, lore_root: Path):
        result = runner.invoke(app, ["quarantine", "show", "nope"])
        assert result.exit_code != 0


class TestClear:
    def test_clear_removes_one(self, lore_root: Path):
        a = _seed(lore_root, composed_text="one")
        _seed(lore_root, composed_text="two")
        result = runner.invoke(app, ["quarantine", "clear", a.id])
        assert result.exit_code == 0, result.output
        remaining = quarantine.list_entries(lore_root=lore_root)
        assert len(remaining) == 1
        assert remaining[0].composed_text == "two"

    def test_clear_missing_errors(self, lore_root: Path):
        result = runner.invoke(app, ["quarantine", "clear", "nope"])
        assert result.exit_code != 0


class TestKill:
    def test_kill_all_purges(self, lore_root: Path):
        _seed(lore_root)
        _seed(lore_root)
        result = runner.invoke(app, ["quarantine", "kill", "--all", "--yes"])
        assert result.exit_code == 0, result.output
        assert quarantine.list_entries(lore_root=lore_root) == []

    def test_kill_one_by_id(self, lore_root: Path):
        a = _seed(lore_root, composed_text="one")
        _seed(lore_root, composed_text="two")
        result = runner.invoke(app, ["quarantine", "kill", a.id, "--yes"])
        assert result.exit_code == 0, result.output
        remaining = quarantine.list_entries(lore_root=lore_root)
        assert len(remaining) == 1
        assert remaining[0].composed_text == "two"
