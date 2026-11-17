"""`lore.api` — the functions behind the MCP tools, for an agent's script (PRD 0024)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

NAMES = (
    "search",
    "read",
    "drill",
    "repo_docs_list",
    "repo_docs_fetch",
    "context_pack",
    "codemap",
)


def test_api_exports_each_tool_and_documents_it_in_one_line() -> None:
    import lore.api as api

    for name in NAMES:
        assert callable(getattr(api, name)), name
        assert f"{name}(" in api.__doc__, name


def test_api_returns_plain_data_on_an_empty_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    import lore.api as api

    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    monkeypatch.setenv("LORE_ROOT", str(tmp_path / "no-such-vault"))
    monkeypatch.chdir(repo)
    monkeypatch.setattr("lore_core.sources.search_sources", lambda *a, **k: [])

    results = [
        api.context_pack(),
        api.repo_docs_list("adr"),
        api.repo_docs_fetch("adr", "0001-x"),
        api.codemap("top"),
        api.search("anything"),
        api.read("x"),
        api.drill("anything"),
    ]
    for r in results:
        json.dumps(r)  # plain data: dicts, lists, strings, numbers
        assert isinstance(r, dict)
    assert capsys.readouterr().out == ""


def test_orient_lore_api_snippet_runs_on_a_fixture_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import re

    skill = Path(__file__).resolve().parent.parent / "skills/orient/SKILL.md"
    snippet = re.search(r"<<'PY'\n(.*?)\nPY\n", skill.read_text(), re.S).group(1)

    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "docs/adr").mkdir(parents=True)
    (repo / "docs/adr/0001-board-ledger.md").write_text("# Board ledger\n\nThe ledger body.\n")
    monkeypatch.setenv("LORE_ROOT", str(tmp_path / "no-such-vault"))
    monkeypatch.chdir(repo)
    monkeypatch.setattr("lore_core.sources.search_sources", lambda *a, **k: [])

    exec(snippet, {})
    assert "The ledger body." in capsys.readouterr().out
