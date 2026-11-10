"""`lore setup scope --scaffold-workflow` — the workflow-scaffold onboarding
step of the one onboarding command (sub-issue #171, moved from attach by #470)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from lore_cli.__main__ import app
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "lore" / "wiki" / "ccat").mkdir(parents=True)
    monkeypatch.setenv("LORE_ROOT", str(tmp_path / "lore"))
    repo = tmp_path / "git" / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    monkeypatch.chdir(repo)
    return repo


def _scope(*extra: str):
    return runner.invoke(app, ["setup", "scope", "--yes", "--wiki", "ccat", *extra])


def test_scaffold_flag_creates_docs(repo: Path) -> None:
    result = _scope("--scaffold-workflow")
    assert result.exit_code == 0, result.output
    assert (repo / "docs" / "prd" / "index.md").exists()
    assert (repo / "docs" / "adr" / "index.md").exists()
    assert (repo / "CLAUDE.md").read_text().strip() == "@AGENTS.md"
    # ADR 0015: the scaffold writes the same reading rule SessionStart injects.
    from lore_core.session_start import reading_rule

    assert reading_rule() in (repo / "AGENTS.md").read_text()


def test_without_flag_does_not_scaffold(repo: Path) -> None:
    result = _scope()
    assert result.exit_code == 0, result.output
    assert not (repo / "docs" / "prd").exists()


def test_scaffold_flag_idempotent_on_rerun(repo: Path) -> None:
    assert _scope("--scaffold-workflow").exit_code == 0
    before = (repo / "docs" / "prd" / "index.md").read_text()
    second = _scope("--scaffold-workflow")
    assert second.exit_code == 0, second.output
    assert (repo / "docs" / "prd" / "index.md").read_text() == before


def test_scaffold_flag_writes_the_index_block(repo: Path) -> None:
    (repo / "docs" / "adr").mkdir(parents=True)
    (repo / "docs" / "adr" / "0001-one-home.md").write_text("# ADR 0001: One home per fact\n")
    assert _scope("--scaffold-workflow").exit_code == 0
    agents = (repo / "AGENTS.md").read_text()
    assert "<!-- lore index: start" in agents and "<!-- lore index: end -->" in agents
    assert "ADR 0001: One home per fact" in agents
    assert runner.invoke(app, ["index", "--check"]).exit_code == 0


def test_scaffold_flag_on_a_repo_without_inputs_writes_no_index_block(repo: Path) -> None:
    assert _scope("--scaffold-workflow").exit_code == 0
    assert "lore index" not in (repo / "AGENTS.md").read_text()


def test_scaffold_adr_index_names_the_adr_template_sections(repo: Path) -> None:
    assert _scope("--scaffold-workflow").exit_code == 0
    stub = (repo / "docs" / "adr" / "index.md").read_text()
    assert "`Holds`" in stub and "`Revisit if`" in stub and "`Alternatives considered`" in stub
    assert "MADR" not in stub and "Consequences" not in stub
