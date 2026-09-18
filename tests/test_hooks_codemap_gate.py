"""SessionStart refreshes CODEMAP.md only inside a git work tree.

Outside git the codemap falls back to a full filesystem walk plus a
content hash of every file. Started in a home directory that walk covers
hundreds of gigabytes and pins a CPU core until the hook is killed, on
every session start. The hook must skip the refresh there.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from lore_cli import hooks
from lore_core import codemap as cm


def _git_repo(root: Path) -> None:
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True, capture_output=True)


def test_refresh_codemap_skips_non_git_directory(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    walked = []
    monkeypatch.setattr(cm, "_walk_files", lambda root: walked.append(root) or [])

    hooks._refresh_codemap(tmp_path)

    assert walked == [], "non-git cwd must never trigger the filesystem walk"
    assert not (tmp_path / cm.MAP_FILENAME).exists()


def test_refresh_codemap_generates_inside_git_work_tree(tmp_path: Path) -> None:
    _git_repo(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")

    hooks._refresh_codemap(tmp_path)

    assert (tmp_path / cm.MAP_FILENAME).exists()
