"""Tests for `lore config get/set/unset/edit` — writable, validated config."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from lore_cli.config_cmd import app
from lore_core.config import user_config_path
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture(autouse=True)
def _person_config_dir() -> None:
    """`lore config set|unset|edit` write the person's config (PRD 0016)."""
    user_config_path().parent.mkdir(parents=True, exist_ok=True)


def _wiki_dir(lore_root: Path, name: str = "private") -> Path:
    d = lore_root / "wiki" / name
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


def test_get_single_key_root(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    result = runner.invoke(app, ["get", "feedback.retrieval_misses"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "false" in result.output


def test_get_no_path_shows_resolved_with_provenance(tmp_path: Path, monkeypatch):
    (tmp_path / ".lore").mkdir()
    user_config_path().write_text("user:\n  display_name: Ada\n")
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    result = runner.invoke(app, ["get"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "user.display_name" in result.output
    assert "Ada" in result.output


def test_get_single_key_wiki(tmp_path: Path, monkeypatch):
    wiki_dir = _wiki_dir(tmp_path)
    (wiki_dir / ".lore-wiki.yml").write_text("git:\n  auto_push: true\n")
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    result = runner.invoke(
        app, ["get", "git.auto_push", "--wiki", "private"], catch_exceptions=False
    )
    assert result.exit_code == 0, result.output
    assert "true" in result.output


def test_get_unknown_wiki_errors(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    result = runner.invoke(app, ["get", "git.auto_push", "--wiki", "nope"], catch_exceptions=False)
    assert result.exit_code != 0
    assert "nope" in result.output


# ---------------------------------------------------------------------------
# set
# ---------------------------------------------------------------------------


def test_set_root_persists(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    result = runner.invoke(app, ["set", "feedback.retrieval_misses", "true"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    cfg = yaml.safe_load(user_config_path().read_text())
    assert cfg["feedback"]["retrieval_misses"] is True


def test_set_wiki_persists(tmp_path: Path, monkeypatch):
    wiki_dir = _wiki_dir(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    result = runner.invoke(
        app, ["set", "git.auto_push", "true", "--wiki", "private"], catch_exceptions=False
    )
    assert result.exit_code == 0, result.output
    cfg = yaml.safe_load((wiki_dir / ".lore-wiki.yml").read_text())
    assert cfg["git"]["auto_push"] is True


def test_set_unknown_key_rejected_names_suggestion_and_leaves_file_unchanged(
    tmp_path: Path, monkeypatch
):
    (tmp_path / ".lore").mkdir()
    cfg_path = user_config_path()
    cfg_path.write_text("feedback:\n  retrieval_misses: true\n")
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    before = cfg_path.read_text()
    result = runner.invoke(app, ["set", "feedback.retrieval_mises", "true"], catch_exceptions=False)
    assert result.exit_code != 0
    assert "feedback.retrieval_misses" in result.output  # named alternative
    assert cfg_path.read_text() == before


def test_set_invalid_value_rejected_names_type_and_leaves_file_unchanged(
    tmp_path: Path, monkeypatch
):
    (tmp_path / ".lore").mkdir()
    cfg_path = user_config_path()
    cfg_path.write_text("feedback:\n  retrieval_misses: true\n")
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    before = cfg_path.read_text()
    result = runner.invoke(app, ["set", "feedback.retrieval_misses", "notabool"], catch_exceptions=False)
    assert result.exit_code != 0
    assert "bool" in result.output
    assert cfg_path.read_text() == before


# ---------------------------------------------------------------------------
# unset
# ---------------------------------------------------------------------------


def test_unset_root_reverts_to_default(tmp_path: Path, monkeypatch):
    (tmp_path / ".lore").mkdir()
    cfg_path = user_config_path()
    cfg_path.write_text("feedback:\n  retrieval_misses: true\n")
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    result = runner.invoke(app, ["unset", "feedback.retrieval_misses"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    cfg = yaml.safe_load(cfg_path.read_text()) or {}
    assert "feedback" not in cfg


def test_unset_wiki_reverts_to_default(tmp_path: Path, monkeypatch):
    wiki_dir = _wiki_dir(tmp_path)
    (wiki_dir / ".lore-wiki.yml").write_text("git:\n  auto_push: true\n")
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    result = runner.invoke(
        app, ["unset", "git.auto_push", "--wiki", "private"], catch_exceptions=False
    )
    assert result.exit_code == 0, result.output
    cfg = yaml.safe_load((wiki_dir / ".lore-wiki.yml").read_text()) or {}
    assert "git" not in cfg


def test_unset_unknown_key_rejected(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    result = runner.invoke(app, ["unset", "feedback.no_such_field"], catch_exceptions=False)
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# edit
# ---------------------------------------------------------------------------


def test_edit_no_changes_is_a_noop(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    monkeypatch.setattr("click.edit", lambda **kwargs: None)  # simulate untouched file
    result = runner.invoke(app, ["edit"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "no changes" in result.output.lower()


def test_edit_valid_change_saves(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))

    def fake_edit(*, filename=None, **kwargs):
        Path(filename).write_text("feedback:\n  retrieval_misses: true\n")

    monkeypatch.setattr("click.edit", fake_edit)
    result = runner.invoke(app, ["edit"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    cfg = yaml.safe_load(user_config_path().read_text())
    assert cfg["feedback"]["retrieval_misses"] is True


def test_edit_invalid_change_abort_reverts_file(tmp_path: Path, monkeypatch):
    (tmp_path / ".lore").mkdir()
    cfg_path = user_config_path()
    cfg_path.write_text("feedback:\n  retrieval_misses: true\n")
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))

    def fake_edit(*, filename=None, **kwargs):
        Path(filename).write_text("feedback:\n  retrieval_misses: notabool\n")

    monkeypatch.setattr("click.edit", fake_edit)
    monkeypatch.setattr("typer.confirm", lambda *a, **k: False)  # decline re-edit
    result = runner.invoke(app, ["edit"], catch_exceptions=False)
    assert result.exit_code != 0
    assert cfg_path.read_text() == "feedback:\n  retrieval_misses: true\n"  # reverted


def test_edit_invalid_then_reedit_to_valid_saves(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    attempts = iter(
        [
            "feedback:\n  retrieval_misses: notabool\n",  # first pass: invalid
            "feedback:\n  retrieval_misses: true\n",  # second pass: valid
        ]
    )

    def fake_edit(*, filename=None, **kwargs):
        Path(filename).write_text(next(attempts))

    monkeypatch.setattr("click.edit", fake_edit)
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)  # accept re-edit
    result = runner.invoke(app, ["edit"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    cfg = yaml.safe_load(user_config_path().read_text())
    assert cfg["feedback"]["retrieval_misses"] is True


def test_edit_wiki_target(tmp_path: Path, monkeypatch):
    wiki_dir = _wiki_dir(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))

    def fake_edit(*, filename=None, **kwargs):
        Path(filename).write_text("git:\n  auto_push: true\n")

    monkeypatch.setattr("click.edit", fake_edit)
    result = runner.invoke(app, ["edit", "--wiki", "private"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    cfg = yaml.safe_load((wiki_dir / ".lore-wiki.yml").read_text())
    assert cfg["git"]["auto_push"] is True


def test_wiki_option_help_names_the_person_config():
    result = runner.invoke(app, ["get", "--help"], env={"COLUMNS": "200"})
    assert "root config" not in result.output
    assert "person config" in result.output
