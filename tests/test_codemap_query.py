"""Behavioural tests for bounded code-map queries (#167).

``lore_core.codemap.query`` serves symbol-pattern, directory-inventory, and
top-N slices of the code map from an in-memory cache keyed on the
generator's fingerprint (git blob SHAs / content hash — see
``lore_core.codemap.discover``), so repeated queries within one session are
free until a tracked file changes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from lore_core.codemap import query as q

CORE_PY = """\
def helper():
    return 1


def widget():
    helper()
    helper()
    return 2
"""

APP_PY = """\
from core import helper, widget


def run():
    helper()
    widget()
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


def _commit_all(root: Path) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "x")


def _fixture_tree(root: Path) -> None:
    _write(root, "core.py", CORE_PY)
    _write(root, "app.py", APP_PY)


def setup_function() -> None:
    q.clear_cache()


def test_symbols_matches_pattern_bounded(tmp_path: Path) -> None:
    _git_repo(tmp_path)
    _fixture_tree(tmp_path)
    _commit_all(tmp_path)

    result = q.query_symbols(tmp_path, "helper")
    assert result["mode"] == "symbols"
    names = [s["qualname"] for s in result["symbols"]]
    assert names == ["helper"]
    assert result["symbols"][0]["refs"] == 3


def test_symbols_limit_bounds_result(tmp_path: Path) -> None:
    _git_repo(tmp_path)
    _fixture_tree(tmp_path)
    _commit_all(tmp_path)

    result = q.query_symbols(tmp_path, ".", limit=2)
    assert len(result["symbols"]) == 2


def test_directory_inventory_scoped_to_prefix(tmp_path: Path) -> None:
    _git_repo(tmp_path)
    _write(tmp_path, "src/a.py", "x = 1\n")
    _write(tmp_path, "src/sub/b.py", "y = 2\n")
    _write(tmp_path, "docs/readme.md", "hi\n")
    _commit_all(tmp_path)

    result = q.query_directory(tmp_path, "src")
    assert result["mode"] == "directory"
    paths = {d["path"] for d in result["dirs"]}
    assert paths == {"src", "src/sub"}


def test_directory_inventory_no_prefix_returns_top_level(tmp_path: Path) -> None:
    _git_repo(tmp_path)
    _fixture_tree(tmp_path)
    _commit_all(tmp_path)

    result = q.query_directory(tmp_path, None)
    assert result["mode"] == "directory"
    assert any(d["path"] == "(root)" for d in result["dirs"])


def test_top_n_ranked_by_refs(tmp_path: Path) -> None:
    _git_repo(tmp_path)
    _fixture_tree(tmp_path)
    _commit_all(tmp_path)

    result = q.query_top(tmp_path, limit=1)
    assert result["mode"] == "top"
    assert result["symbols"][0]["qualname"] == "helper"
    assert len(result["symbols"]) == 1


def test_cache_hit_avoids_rebuild(tmp_path: Path, monkeypatch) -> None:
    _git_repo(tmp_path)
    _fixture_tree(tmp_path)
    _commit_all(tmp_path)

    calls = []
    from lore_core import codemap as cm

    real_build = cm.build_code_map

    def counting_build(root):
        calls.append(root)
        return real_build(root)

    monkeypatch.setattr(cm, "build_code_map", counting_build)

    q.query_top(tmp_path, limit=5)
    q.query_top(tmp_path, limit=5)
    assert len(calls) == 1


def test_cache_invalidated_on_change(tmp_path: Path) -> None:
    _git_repo(tmp_path)
    _fixture_tree(tmp_path)
    _commit_all(tmp_path)

    first = q.query_top(tmp_path, limit=5)
    _write(tmp_path, "extra.py", "def brandnew():\n    return 1\n")
    _commit_all(tmp_path)
    second = q.query_top(tmp_path, limit=5)

    first_names = {s["qualname"] for s in first["symbols"]}
    second_names = {s["qualname"] for s in second["symbols"]}
    assert "brandnew" not in first_names
    assert "brandnew" in second_names


def test_cold_start_generates(tmp_path: Path) -> None:
    """No cache entry yet — first query builds the map from scratch."""
    _git_repo(tmp_path)
    _fixture_tree(tmp_path)
    _commit_all(tmp_path)

    result = q.query_symbols(tmp_path, "widget")
    assert result["symbols"][0]["qualname"] == "widget"
