"""MCP pull tools for repo ADRs/PRDs — `lore_repo_docs_list` / `lore_repo_docs_fetch`.

Pull-only: these tools read a connected repo's ratified decisions from
their conventional homes (`docs/adr/`, `docs/prd/`) on explicit MCP
call. Nothing here is wired into SessionStart/ambient context — that
stays the workflow plugin's job during coexistence (PRD 0001,
"Ambient vs pull").
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lore_mcp.server import _dispatch, _tool_schema, handle_repo_docs_fetch, handle_repo_docs_list


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


# ---- handle_repo_docs_list ----


def test_list_returns_adr_entries(tmp_path: Path) -> None:
    _write(tmp_path / "docs/adr/0001-x.md", "---\ntitle: X\nstatus: accepted\n---\nbody\n")
    result = handle_repo_docs_list(kind="adr", repo_path=str(tmp_path))
    assert result["schema"] == "lore.repo_docs.list/1"
    assert result["kind"] == "adr"
    assert result["home"] == "docs/adr"
    assert result["exists"] is True
    assert result["entries"] == [
        {"path": "docs/adr/0001-x.md", "title": "X", "status": "accepted", "is_index": False}
    ]


def test_list_returns_prd_entries(tmp_path: Path) -> None:
    _write(tmp_path / "docs/prd/0001-y.md", "---\ntitle: Y\nstatus: draft\n---\nbody\n")
    result = handle_repo_docs_list(kind="prd", repo_path=str(tmp_path))
    assert result["kind"] == "prd"
    assert result["entries"][0]["title"] == "Y"


def test_list_graceful_empty_when_home_missing(tmp_path: Path) -> None:
    """A repo with no docs/adr or docs/prd is not an error — empty result."""
    result = handle_repo_docs_list(kind="adr", repo_path=str(tmp_path))
    assert "error" not in result
    assert result["exists"] is False
    assert result["entries"] == []

    result = handle_repo_docs_list(kind="prd", repo_path=str(tmp_path))
    assert "error" not in result
    assert result["exists"] is False
    assert result["entries"] == []


def test_list_invalid_kind_error_envelope(tmp_path: Path) -> None:
    result = handle_repo_docs_list(kind="bogus", repo_path=str(tmp_path))
    assert result["error"]["code"] == "invalid_kind"


def test_list_repo_not_found_when_not_a_git_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("lore_core.git.git_repo_root", lambda cwd: None)
    result = handle_repo_docs_list(kind="adr")
    assert result["error"]["code"] == "repo_not_found"
    assert "next" in result["error"]


# ---- handle_repo_docs_fetch ----


def test_fetch_by_bare_slug(tmp_path: Path) -> None:
    _write(
        tmp_path / "docs/adr/0001-x.md",
        "---\ntitle: X\nstatus: accepted\n---\nDecision body.\n",
    )
    result = handle_repo_docs_fetch(kind="adr", path="0001-x", repo_path=str(tmp_path))
    assert result["schema"] == "lore.repo_docs.fetch/1"
    assert result["path"] == "docs/adr/0001-x.md"
    assert result["title"] == "X"
    assert result["status"] == "accepted"
    assert "Decision body." in result["content"]


def test_fetch_by_full_relative_path(tmp_path: Path) -> None:
    _write(tmp_path / "docs/prd/0001-y.md", "---\ntitle: Y\n---\nbody\n")
    result = handle_repo_docs_fetch(kind="prd", path="docs/prd/0001-y.md", repo_path=str(tmp_path))
    assert result["path"] == "docs/prd/0001-y.md"


def test_fetch_index_file(tmp_path: Path) -> None:
    _write(tmp_path / "docs/adr/README.md", "# ADR index\n")
    result = handle_repo_docs_fetch(kind="adr", path="README", repo_path=str(tmp_path))
    assert "ADR index" in result["content"]


def test_fetch_not_found_error_envelope(tmp_path: Path) -> None:
    (tmp_path / "docs/adr").mkdir(parents=True)
    result = handle_repo_docs_fetch(kind="adr", path="missing", repo_path=str(tmp_path))
    assert result["error"]["code"] == "doc_not_found"


def test_fetch_path_escape_does_not_leak_outside_home(tmp_path: Path) -> None:
    (tmp_path / "docs/adr").mkdir(parents=True)
    (tmp_path / "secret.md").write_text("top secret\n")
    result = handle_repo_docs_fetch(kind="adr", path="../../secret", repo_path=str(tmp_path))
    assert result["error"]["code"] == "doc_not_found"


def test_fetch_invalid_kind_error_envelope(tmp_path: Path) -> None:
    result = handle_repo_docs_fetch(kind="bogus", path="x", repo_path=str(tmp_path))
    assert result["error"]["code"] == "invalid_kind"


def test_fetch_repo_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("lore_core.git.git_repo_root", lambda cwd: None)
    result = handle_repo_docs_fetch(kind="adr", path="0001-x")
    assert result["error"]["code"] == "repo_not_found"


# ---- tool schema + dispatch registration ----


def test_tools_registered_in_schema() -> None:
    names = {t["name"] for t in _tool_schema()}
    assert "lore_repo_docs_list" in names
    assert "lore_repo_docs_fetch" in names


def test_dispatch_routes_list_and_fetch(tmp_path: Path) -> None:
    _write(tmp_path / "docs/adr/0001-x.md", "---\ntitle: X\n---\nbody\n")
    listed = _dispatch("lore_repo_docs_list", {"kind": "adr", "repo_path": str(tmp_path)})
    assert listed["entries"][0]["path"] == "docs/adr/0001-x.md"

    fetched = _dispatch(
        "lore_repo_docs_fetch",
        {"kind": "adr", "path": "0001-x", "repo_path": str(tmp_path)},
    )
    assert fetched["title"] == "X"
