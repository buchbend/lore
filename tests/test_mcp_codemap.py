"""MCP tool ``lore_codemap`` — bounded code-map query slices (#167).

Pull-only: mirrors the repo-docs tools' contract (explicit `repo_path`
wins, otherwise auto-detect the git repo containing the server's cwd).
Returns bounded slices only, never the full generated map.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from lore_core.codemap import query as codemap_query
from lore_mcp.server import _dispatch, _tool_schema, handle_codemap

CORE_PY = """\
def helper():
    return 1


def widget():
    helper()
    helper()
    return 2
"""


def _write(root: Path, relpath: str, content: str) -> None:
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _git_repo(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    _write(root, "core.py", CORE_PY)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "x")


def setup_function() -> None:
    codemap_query.clear_cache()


def test_tool_schema_includes_lore_codemap() -> None:
    names = {t["name"] for t in _tool_schema()}
    assert "lore_codemap" in names


def test_symbols_mode(tmp_path: Path) -> None:
    _git_repo(tmp_path)
    result = handle_codemap(mode="symbols", pattern="helper", repo_path=str(tmp_path))
    assert result["schema"] == "lore.codemap/1"
    assert result["symbols"][0]["qualname"] == "helper"


def test_symbols_mode_requires_pattern(tmp_path: Path) -> None:
    _git_repo(tmp_path)
    result = handle_codemap(mode="symbols", repo_path=str(tmp_path))
    assert result["error"]["code"] == "pattern_required"


def test_directory_mode(tmp_path: Path) -> None:
    _git_repo(tmp_path)
    result = handle_codemap(mode="directory", repo_path=str(tmp_path))
    assert any(d["path"] == "(root)" for d in result["dirs"])


def test_top_mode(tmp_path: Path) -> None:
    _git_repo(tmp_path)
    result = handle_codemap(mode="top", limit=1, repo_path=str(tmp_path))
    assert result["symbols"][0]["qualname"] == "helper"


def test_invalid_mode_error_envelope(tmp_path: Path) -> None:
    result = handle_codemap(mode="bogus", repo_path=str(tmp_path))
    assert result["error"]["code"] == "invalid_mode"


def test_repo_not_found_when_not_a_git_repo(monkeypatch) -> None:
    monkeypatch.setattr("lore_core.git.git_repo_root", lambda cwd: None)
    result = handle_codemap(mode="top")
    assert result["error"]["code"] == "repo_not_found"
    assert "next" in result["error"]


def test_dispatch_routes_to_handle_codemap(tmp_path: Path) -> None:
    _git_repo(tmp_path)
    result = _dispatch("lore_codemap", {"mode": "top", "repo_path": str(tmp_path)})
    assert result["schema"] == "lore.codemap/1"
