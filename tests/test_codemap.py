"""Behavioural tests for the deterministic code-map generator (#165).

Two layers over ONE gitignore-aware discovery pass:

1. Repository inventory — per-directory file counts, sizes, top extensions.
2. Ranked Python symbol index (stdlib ``ast``), ported from the frozen
   ccat-agent-workflow ``scripts/code_map.py``.

Discovery is ``git ls-files`` when the tree is a git repo (so a gitignored
``.py`` never reaches the symbol layer), with a plain-walk fallback otherwise.
The fingerprint rides git blob SHAs (index-staged content) so any tracked-file
change trips regeneration; an unchanged fingerprint is a silent no-op.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from lore_core import codemap as cm


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


# Reference gradient: helper() 3x, widget() 1x, run() 0x.
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


def _fixture_tree(root: Path) -> None:
    _write(root, "core.py", CORE_PY)
    _write(root, "app.py", APP_PY)


# --------------------------------------------------------------------------
# Symbol extraction (ported verbatim)
# --------------------------------------------------------------------------


def test_extract_symbols_kinds() -> None:
    src = "class Foo:\n    def bar(self):\n        pass\n\n\ndef baz():\n    pass\n"
    by_name = {s.name: s for s in cm.extract_symbols("m.py", src)}
    assert by_name["Foo"].kind == "class"
    assert by_name["Foo"].lineno == 1
    assert by_name["bar"].kind == "method"
    assert by_name["bar"].qualname == "Foo.bar"
    assert by_name["baz"].kind == "function"


def test_extract_symbols_survives_syntax_error() -> None:
    assert cm.extract_symbols("broken.py", "def (:\n") == []


# --------------------------------------------------------------------------
# Discovery — one pass, gitignore-aware, non-git fallback
# --------------------------------------------------------------------------


def test_discover_walk_fallback_skips_infra_dirs(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "x = 1\n")
    _write(tmp_path, "pkg/b.py", "y = 2\n")
    _write(tmp_path, "__pycache__/c.py", "z = 3\n")
    _write(tmp_path, ".claude/worktrees/w/d.py", "w = 4\n")
    disc = cm.discover(tmp_path)
    assert disc.source == "walk"
    assert set(disc.files) == {"a.py", "pkg/b.py"}


def test_discover_git_respects_gitignore(tmp_path: Path) -> None:
    _git_repo(tmp_path)
    _write(tmp_path, "kept.py", "def kept():\n    pass\n")
    _write(tmp_path, "secret.py", "def secret():\n    pass\n")
    _write(tmp_path, ".gitignore", "secret.py\n")
    _commit_all(tmp_path)
    disc = cm.discover(tmp_path)
    assert disc.source == "git"
    assert "kept.py" in disc.files
    assert "secret.py" not in disc.files


def test_gitignored_py_never_in_symbols(tmp_path: Path) -> None:
    _git_repo(tmp_path)
    _write(tmp_path, "kept.py", "def kept():\n    pass\n")
    _write(tmp_path, "secret.py", "def secret():\n    pass\n")
    _write(tmp_path, ".gitignore", "secret.py\n")
    _commit_all(tmp_path)
    names = {s.name for s in cm.build_code_map(tmp_path).symbols}
    assert "kept" in names
    assert "secret" not in names


# --------------------------------------------------------------------------
# Ranking
# --------------------------------------------------------------------------


def test_ranked_by_reference_count(tmp_path: Path) -> None:
    _fixture_tree(tmp_path)
    code_map = cm.build_code_map(tmp_path)
    by_name = {s.name: s for s in code_map.symbols}
    assert by_name["helper"].refs == 3
    assert by_name["widget"].refs == 1
    assert by_name["run"].refs == 0
    order = [s.name for s in code_map.symbols]
    assert order.index("helper") < order.index("widget") < order.index("run")
    assert code_map.symbols[0].name == "helper"


def test_build_code_map_records_location(tmp_path: Path) -> None:
    _fixture_tree(tmp_path)
    by_name = {s.name: s for s in cm.build_code_map(tmp_path).symbols}
    assert by_name["helper"].relpath == "core.py"
    assert by_name["helper"].lineno == 1


# --------------------------------------------------------------------------
# Inventory layer
# --------------------------------------------------------------------------


def test_inventory_counts_all_files_not_just_python(tmp_path: Path) -> None:
    _write(tmp_path, "core.py", CORE_PY)
    _write(tmp_path, "README.md", "# hi\n")
    _write(tmp_path, "pkg/mod.py", "a = 1\n")
    _write(tmp_path, "pkg/data.json", "{}\n")
    inv = cm.build_code_map(tmp_path).inventory
    assert inv.total_files == 4
    dirs = {d.path: d for d in inv.dirs}
    assert dirs["(root)"].file_count == 2
    assert dirs["pkg"].file_count == 2
    ext = dict(inv.ext_counts)
    assert ext[".py"] == 2


def test_inventory_bounded_rows(tmp_path: Path) -> None:
    for i in range(cm.MAX_DIR_ROWS + 20):
        _write(tmp_path, f"d{i:03d}/f.txt", "x\n")
    inv = cm.build_code_map(tmp_path).inventory
    assert len(inv.dirs) <= cm.MAX_DIR_ROWS


def test_inventory_rendered_in_map(tmp_path: Path) -> None:
    _fixture_tree(tmp_path)
    cm.generate(tmp_path)
    text = (tmp_path / cm.MAP_FILENAME).read_text(encoding="utf-8")
    assert "Repository inventory" in text
    assert "Ranked symbols" in text


# --------------------------------------------------------------------------
# Rendered map + fingerprint round-trip
# --------------------------------------------------------------------------


def test_generate_creates_map_with_fingerprint(tmp_path: Path) -> None:
    _fixture_tree(tmp_path)
    result = cm.generate(tmp_path)
    assert result.status == "created"
    assert result.wrote is True
    text = (tmp_path / cm.MAP_FILENAME).read_text(encoding="utf-8")
    assert cm.read_fingerprint(text) == result.fingerprint
    assert "helper" in text and "widget" in text


def test_generate_is_idempotent_byte_for_byte(tmp_path: Path) -> None:
    _fixture_tree(tmp_path)
    cm.generate(tmp_path)
    map_path = tmp_path / cm.MAP_FILENAME
    first = map_path.read_text(encoding="utf-8")
    cm.generate(tmp_path)
    assert map_path.read_text(encoding="utf-8") == first


# --------------------------------------------------------------------------
# No-op fast path
# --------------------------------------------------------------------------


def test_unchanged_tree_is_no_op_walk(tmp_path: Path) -> None:
    _fixture_tree(tmp_path)
    assert cm.generate(tmp_path).status == "created"
    map_path = tmp_path / cm.MAP_FILENAME
    old = 1_000_000_000
    os.utime(map_path, (old, old))
    second = cm.generate(tmp_path)
    assert second.status == "up-to-date"
    assert second.wrote is False
    assert map_path.stat().st_mtime == old, "no-op run must not rewrite the map"


def test_git_fingerprint_no_op_and_regen(tmp_path: Path) -> None:
    _git_repo(tmp_path)
    _fixture_tree(tmp_path)
    _commit_all(tmp_path)
    assert cm.generate(tmp_path).status == "created"
    # Unchanged staged tree -> no-op.
    assert cm.generate(tmp_path).status == "up-to-date"
    # A staged change flips the blob SHA -> regen.
    _write(tmp_path, "core.py", CORE_PY + "\n\ndef added_symbol():\n    return 3\n")
    _git(tmp_path, "add", "-A")
    result = cm.generate(tmp_path)
    assert result.status == "updated"
    assert "core.py::added_symbol" in result.added


def test_changed_tree_rewrites_and_reports_delta_walk(tmp_path: Path) -> None:
    _fixture_tree(tmp_path)
    cm.generate(tmp_path)
    _write(tmp_path, "core.py", CORE_PY + "\n\ndef added_symbol():\n    return 3\n")
    result = cm.generate(tmp_path)
    assert result.status == "updated"
    assert "core.py::added_symbol" in result.added
    assert result.removed == ()


def test_removed_symbol_reported_in_delta(tmp_path: Path) -> None:
    _fixture_tree(tmp_path)
    cm.generate(tmp_path)
    (tmp_path / "app.py").unlink()
    result = cm.generate(tmp_path)
    assert result.status == "updated"
    assert "app.py::run" in result.removed


# --------------------------------------------------------------------------
# Atomic write + empty repo
# --------------------------------------------------------------------------


def test_atomic_write_leaves_no_temp_files(tmp_path: Path) -> None:
    _fixture_tree(tmp_path)
    cm.generate(tmp_path)
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".codemap")]
    assert leftovers == []


def test_empty_repo_produces_valid_map(tmp_path: Path) -> None:
    result = cm.generate(tmp_path)
    assert result.status == "created"
    text = (tmp_path / cm.MAP_FILENAME).read_text(encoding="utf-8")
    assert cm.read_fingerprint(text) == result.fingerprint


# --------------------------------------------------------------------------
# CLI entrypoint
# --------------------------------------------------------------------------


def test_cli_returns_zero_and_creates_map(tmp_path: Path) -> None:
    assert cm.main([str(tmp_path)]) == 0
    assert (tmp_path / cm.MAP_FILENAME).exists()


def test_cli_quiet_is_silent_on_no_op(tmp_path: Path, capsys) -> None:
    _fixture_tree(tmp_path)
    cm.main([str(tmp_path), "--quiet"])
    capsys.readouterr()
    assert cm.main([str(tmp_path), "--quiet"]) == 0
    assert capsys.readouterr().out == ""
