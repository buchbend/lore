"""Tests for the interactive `lore attach` wizard."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from lore_cli.__main__ import app
from lore_core.offer import FILENAME
from lore_core.state.attachments import AttachmentsFile
from lore_core.state.scopes import ScopesFile

runner = CliRunner(mix_stderr=False)


@pytest.fixture
def lore_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    lore_root = tmp_path / "lore-root"
    lore_root.mkdir()
    (lore_root / ".lore").mkdir()
    (lore_root / "wiki").mkdir()
    for w in ("private", "ccat", "science"):
        (lore_root / "wiki" / w).mkdir()
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    monkeypatch.setattr("lore_core.config.get_lore_root", lambda: lore_root)
    monkeypatch.setattr("lore_core.config.get_wiki_root", lambda: lore_root / "wiki")
    monkeypatch.setattr("lore_cli.attach_cmd._is_interactive", lambda: True)
    return lore_root


def _write_offer(dir_: Path, *, wiki: str = "ccat", scope: str = "ccat:backend",
                 backend: str = "github") -> None:
    (dir_ / FILENAME).write_text(
        f"wiki: {wiki}\nscope: {scope}\nbackend: {backend}\n"
    )


# ---- Config detected flow ----

def test_use_as_is(lore_env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_offer(repo)
    result = runner.invoke(app, ["attach", "--cwd", str(repo)], input="u\n")
    assert result.exit_code == 0, result.output
    assert "Attached" in result.output

    af = AttachmentsFile(lore_env)
    af.load()
    rows = af.all()
    assert len(rows) == 1
    assert rows[0].wiki == "ccat"
    assert rows[0].scope == "ccat:backend"


def test_skip(lore_env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_offer(repo)
    result = runner.invoke(app, ["attach", "--cwd", str(repo)], input="s\n")
    assert result.exit_code == 0, result.output
    assert "Declined" in result.output

    af = AttachmentsFile(lore_env)
    af.load()
    assert len(af.all()) == 0


def test_customize(lore_env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_offer(repo, wiki="ccat", scope="ccat:backend", backend="github")

    # c=customize, Enter=keep default wiki (ccat), "ccat:frontend"=override scope,
    # Enter=keep backend, Enter=no .lore.yml, y=proceed
    input_lines = "c\n\nccat:frontend\n\n\ny\n"
    result = runner.invoke(app, ["attach", "--cwd", str(repo)], input=input_lines)
    assert result.exit_code == 0, result.output
    assert "Attached" in result.output

    af = AttachmentsFile(lore_env)
    af.load()
    rows = af.all()
    assert len(rows) == 1
    assert rows[0].scope == "ccat:frontend"


# ---- Manual flow (no .lore.yml) ----

def test_manual_pick_existing(lore_env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    # wikis sorted: ccat=1, private=2, science=3
    # 1=ccat wiki, "ccat:newscope"=scope (bare input, no scopes in registry),
    # Enter=default backend, n=no .lore.yml, y=proceed
    input_lines = "1\nccat:newscope\n\nn\ny\n"
    result = runner.invoke(app, ["attach", "--cwd", str(repo)], input=input_lines)
    assert result.exit_code == 0, result.output
    assert "Attached" in result.output

    af = AttachmentsFile(lore_env)
    af.load()
    rows = af.all()
    assert len(rows) == 1
    assert rows[0].wiki == "ccat"
    assert rows[0].scope == "ccat:newscope"


def test_manual_custom_wiki(lore_env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    # c=custom wiki, "newwiki"=name, "newwiki:sub"=scope (bare input),
    # Enter=default backend, n=no .lore.yml, y=proceed
    input_lines = "c\nnewwiki\nnewwiki:sub\n\nn\ny\n"
    result = runner.invoke(app, ["attach", "--cwd", str(repo)], input=input_lines)
    assert result.exit_code == 0, result.output
    assert "Attached" in result.output

    af = AttachmentsFile(lore_env)
    af.load()
    rows = af.all()
    assert len(rows) == 1
    assert rows[0].wiki == "newwiki"
    assert rows[0].scope == "newwiki:sub"


def test_manual_write_lore_yml(lore_env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    # wikis sorted: ccat=1, private=2, science=3
    # 2=private wiki, "lore:test"=scope (bare input), Enter=default backend,
    # y=write .lore.yml, y=proceed
    input_lines = "2\nlore:test\n\ny\ny\n"
    result = runner.invoke(app, ["attach", "--cwd", str(repo)], input=input_lines)
    assert result.exit_code == 0, result.output
    assert (repo / FILENAME).exists()


def test_abort(lore_env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    # 2=private wiki, "test"=scope (bare input), Enter=backend, n=no yml, n=abort
    input_lines = "2\ntest\n\nn\nn\n"
    result = runner.invoke(app, ["attach", "--cwd", str(repo)], input=input_lines)
    assert result.exit_code == 0
    assert "Aborted" in result.output

    af = AttachmentsFile(lore_env)
    af.load()
    assert len(af.all()) == 0


# ---- Already attached ----

def test_already_attached_decline_reattach(lore_env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_offer(repo)
    # First attach
    runner.invoke(app, ["attach", "--cwd", str(repo)], input="u\n")

    # Second attach — decline re-attach
    result = runner.invoke(app, ["attach", "--cwd", str(repo)], input="n\n")
    assert result.exit_code == 0
    assert "Already attached" in result.output


def test_parent_attached_shows_info_but_continues(lore_env: Path, tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    _write_offer(parent, wiki="ccat", scope="ccat")
    runner.invoke(app, ["attach", "--cwd", str(parent)], input="u\n")

    child = parent / "child"
    child.mkdir()
    _write_offer(child, wiki="ccat", scope="ccat:child")
    # Should show parent info but proceed to the config flow, then use as-is
    result = runner.invoke(app, ["attach", "--cwd", str(child)], input="u\n")
    assert result.exit_code == 0, result.output
    assert "parent attachment" in result.output.lower() or "Covered by" in result.output
    assert "Attached" in result.output


# ---- Subcommands still work ----

def test_subcommand_accept_still_works(lore_env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_offer(repo)
    result = runner.invoke(app, ["attach", "accept", "--cwd", str(repo)])
    assert result.exit_code == 0, result.output
    assert "Attached" in result.output


def test_subcommand_manual_still_works(lore_env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    result = runner.invoke(
        app, ["attach", "manual", "--wiki", "private", "--scope", "test", "--cwd", str(repo)]
    )
    assert result.exit_code == 0, result.output
    assert "Attached" in result.output
