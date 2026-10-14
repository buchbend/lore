"""`lore roadmap add` (PRD 0018 § lore roadmap add) and the ADR 0018 boundary."""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest
from lore_cli.__main__ import main

LIB = Path(__file__).resolve().parent.parent / "lib"


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = tmp_path / "widget"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    monkeypatch.chdir(repo)
    return repo


def _add(*args: str) -> int:
    try:
        return int(main(["roadmap", "add", *args]) or 0)
    except SystemExit as exc:
        return int(exc.code or 0)


def test_missing_file_gets_skeleton_and_item(repo, capsys):
    assert _add("Faster search", "Cache it.") == 0
    text = (repo / "ROADMAP.md").read_text()
    assert text.startswith("# Roadmap\n")
    assert text.rstrip().endswith("## Not placed yet\n\n- **Faster search.** Cache it.")
    assert "ROADMAP.md" in capsys.readouterr().out


def test_title_only(repo):
    _add("Idea")
    assert (repo / "ROADMAP.md").read_text().rstrip().endswith("- **Idea.**")


def test_missing_section_is_added_at_end(repo):
    (repo / "ROADMAP.md").write_text("# Roadmap\n\nHeading north.\n\n- **A.** b\n")
    _add("New")
    text = (repo / "ROADMAP.md").read_text()
    assert text.startswith("# Roadmap\n\nHeading north.\n\n- **A.** b\n")
    assert text.rstrip().endswith("## Not placed yet\n\n- **New.**")


def test_append_to_existing_list_before_next_heading(repo):
    (repo / "ROADMAP.md").write_text(
        "# Roadmap\n\n## Not placed yet\n\n- **Old.** one\n\n## Later\n\nx\n"
    )
    _add("New", "two")
    assert (repo / "ROADMAP.md").read_text() == (
        "# Roadmap\n\n## Not placed yet\n\n- **Old.** one\n- **New.** two\n\n## Later\n\nx\n"
    )


def test_component_roadmap_wins(repo, monkeypatch):
    comp = repo / "comp" / "src"
    comp.mkdir(parents=True)
    (repo / "comp" / "ROADMAP.md").write_text("# Roadmap\n\n## Not placed yet\n")
    monkeypatch.chdir(comp)
    _add("Local")
    assert "- **Local.**" in (repo / "comp" / "ROADMAP.md").read_text()
    assert not (repo / "ROADMAP.md").exists()


def test_leaves_git_alone(repo):
    _add("X")
    out = subprocess.run(["git", "log"], cwd=repo, capture_output=True, text=True).stderr
    assert "does not have any commits" in out


def test_outside_repo_exits_nonzero(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    assert _add("X") != 0
    assert len(capsys.readouterr().err.strip().splitlines()) == 1


def _is_pm(name: str) -> bool:
    top = name.split(".")[0]
    return top == "pm_cli" or top.startswith("project_manage")


def test_title_dot_and_empty(repo, capsys):
    assert _add("Faster search.") == 0
    assert "- **Faster search.**" in (repo / "ROADMAP.md").read_text()
    assert ".." not in (repo / "ROADMAP.md").read_text()
    assert _add("  ") == 1
    assert "roadmap add:" in capsys.readouterr().err


def test_invariant_lib_imports_no_project_manage_package():
    """ADR 0018: no module under lib/ imports `pm_cli` (project-manage CLI) or any
    top-level package starting with `project_manage` (backend and its plugins such as
    `project_manage_github`), nor a submodule of them."""
    assert all(_is_pm(n) for n in ("pm_cli.x", "project_manage", "project_manage_github.a"))
    hits = []
    for py in LIB.rglob("*.py"):
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            else:
                continue
            hits += [f"{py}: {n}" for n in names if _is_pm(n)]
    assert not hits, hits
