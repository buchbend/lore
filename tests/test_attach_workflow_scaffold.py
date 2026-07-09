"""`lore attach ... --scaffold-workflow` — the workflow-scaffold onboarding
step folded into the single `lore attach` entry point (sub-issue #171).

No separate `ccat-workflow-init`-style entry point: the scaffold only runs
as an opt-in step of attach.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lore_cli.__main__ import app
from lore_core.state.workflow_scaffold import WorkflowScaffoldFile
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def lore_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    lore_root = tmp_path / "lore-root"
    lore_root.mkdir()
    (lore_root / ".lore").mkdir()
    (lore_root / "wiki").mkdir()
    (lore_root / "wiki" / "ccat").mkdir()
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    monkeypatch.setattr("lore_core.config.get_lore_root", lambda: lore_root)
    monkeypatch.setattr("lore_core.config.get_wiki_root", lambda: lore_root / "wiki")
    return lore_root


def test_manual_scaffold_flag_creates_docs(lore_env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    result = runner.invoke(
        app,
        [
            "attach", "manual",
            "--wiki", "ccat", "--scope", "ccat:backend",
            "--cwd", str(repo), "--scaffold-workflow",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (repo / "docs" / "prd" / "index.md").exists()
    assert (repo / "docs" / "adr" / "index.md").exists()
    assert (repo / "AGENTS.md").exists()
    assert (repo / "CLAUDE.md").read_text().strip() == "@AGENTS.md"


def test_manual_without_flag_does_not_scaffold(lore_env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    result = runner.invoke(
        app,
        ["attach", "manual", "--wiki", "ccat", "--scope", "ccat:backend", "--cwd", str(repo)],
    )
    assert result.exit_code == 0, result.output
    assert not (repo / "docs" / "prd").exists()


def test_manual_scaffold_flag_records_state(lore_env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    runner.invoke(
        app,
        [
            "attach", "manual",
            "--wiki", "ccat", "--scope", "ccat:backend",
            "--cwd", str(repo), "--scaffold-workflow",
        ],
    )
    record = WorkflowScaffoldFile(lore_env)
    record.load()
    assert record.was_scaffolded(repo)


def test_manual_scaffold_flag_idempotent_on_rerun(lore_env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    args = [
        "attach", "manual",
        "--wiki", "ccat", "--scope", "ccat:backend",
        "--cwd", str(repo), "--scaffold-workflow",
    ]
    first = runner.invoke(app, args)
    assert first.exit_code == 0, first.output
    before = (repo / "docs" / "prd" / "index.md").read_text()
    second = runner.invoke(app, args)
    assert second.exit_code == 0, second.output
    after = (repo / "docs" / "prd" / "index.md").read_text()
    assert before == after
