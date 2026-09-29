"""A shipped PRD (ADR 0015, #444).

When an epic merges, `lore workflow prd-ship` marks its PRD `status: shipped`
and adds one line under the title that names the current ADRs. Retrieval ranks
a shipped PRD below ADRs, and SessionStart never injects one.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from lore_cli.__main__ import app
from lore_core.context_pack import gather
from lore_core.repo_docs import list_docs
from lore_core.schema import parse_frontmatter
from typer.testing import CliRunner

runner = CliRunner()

_PRD = """\
---
title: Slim the workflow
status: accepted
epic: https://github.com/acme/widget/issues/443
---

# PRD 0015: Slim the workflow

> Source of truth for this epic.

## Problem

Text.
"""

HISTORICAL = "> Historical. Current decisions: ADR 0014, ADR 0015."


def _prd(tmp_path: Path, name: str = "0015-slim.md", text: str = _PRD) -> Path:
    path = tmp_path / "docs" / "prd" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _ship(path: Path, *adrs: str):
    args = ["workflow", "prd-ship", str(path)]
    for adr in adrs:
        args += ["--adr", adr]
    return runner.invoke(app, args)


# --- the verb --------------------------------------------------------------


def test_prd_ship_sets_status_and_adds_the_historical_line_under_the_title(
    tmp_path: Path,
) -> None:
    path = _prd(tmp_path)
    result = _ship(path, "0014", "0015")
    assert result.exit_code == 0, result.output
    text = path.read_text(encoding="utf-8")
    assert parse_frontmatter(text)["status"] == "shipped"
    assert f"# PRD 0015: Slim the workflow\n\n{HISTORICAL}\n\n> Source of truth" in text


def test_prd_ship_is_idempotent(tmp_path: Path) -> None:
    path = _prd(tmp_path)
    _ship(path, "0014", "0015")
    once = path.read_text(encoding="utf-8")
    result = _ship(path, "0014", "0015")
    assert result.exit_code == 0, result.output
    assert path.read_text(encoding="utf-8") == once


def test_prd_ship_again_replaces_the_adr_list(tmp_path: Path) -> None:
    path = _prd(tmp_path)
    _ship(path, "0014")
    _ship(path, "14", "0016")
    text = path.read_text(encoding="utf-8")
    assert text.count("> Historical.") == 1
    assert "> Historical. Current decisions: ADR 0014, ADR 0016." in text


def test_prd_ship_keeps_the_other_frontmatter_fields(tmp_path: Path) -> None:
    path = _prd(tmp_path)
    _ship(path, "0014")
    fm = parse_frontmatter(path.read_text(encoding="utf-8"))
    assert fm["title"] == "Slim the workflow"
    assert fm["epic"] == "https://github.com/acme/widget/issues/443"


def test_prd_ship_needs_an_adr(tmp_path: Path) -> None:
    path = _prd(tmp_path)
    result = _ship(path)
    assert result.exit_code != 0
    assert path.read_text(encoding="utf-8") == _PRD


def test_prd_ship_fails_on_a_prd_without_a_title(tmp_path: Path) -> None:
    path = _prd(tmp_path, text="---\nstatus: draft\n---\n\nNo heading.\n")
    result = _ship(path, "0014")
    assert result.exit_code == 1
    assert "H1" in result.output


# --- ranking ---------------------------------------------------------------


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _prd(repo, "0001-old.md", _PRD.replace("status: accepted", "status: shipped"))
    _prd(repo, "0002-current.md")
    adr = repo / "docs" / "adr" / "0014-home.md"
    adr.parent.mkdir(parents=True)
    adr.write_text("---\nstatus: accepted\n---\n# ADR 0014\n", encoding="utf-8")
    return repo


def test_repo_docs_list_ranks_a_shipped_prd_last(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    paths = [e["path"] for e in list_docs(repo, "prd")]
    assert paths == ["docs/prd/0002-current.md", "docs/prd/0001-old.md"]


def test_context_pack_ranks_a_shipped_prd_below_adrs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(tmp_path / "no-such-vault"))
    pack = gather(cwd=repo, repo_path=str(repo))
    # Paths only: the entries themselves already sit under `adr` and `prd`,
    # and repeating them doubles the pack's token cost.
    assert pack["ranked"] == [
        "docs/adr/0014-home.md",
        "docs/prd/0002-current.md",
        "docs/prd/0001-old.md",
    ]


# --- SessionStart ----------------------------------------------------------


def test_session_start_never_injects_a_shipped_prd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SessionStart injects no PRD at all today. Pin it for the shipped case."""
    from lore_core import session_start

    repo = _repo(tmp_path)
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    monkeypatch.setattr(session_start, "current_repo", lambda _cwd: None)
    block = {"wiki": wiki.name, "scope": wiki.name, "backend": None, "issues": None, "prs": None}
    out = session_start.session_start_from_lore(str(repo), (repo / "CLAUDE.md", block), tmp_path)
    assert out is not None
    assert "Slim the workflow" not in out
    assert "0001-old" not in out
