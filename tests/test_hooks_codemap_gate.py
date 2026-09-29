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


def test_session_start_refreshes_codemap_in_background(tmp_path: Path, monkeypatch) -> None:
    """The refresh parses every file; SessionStart hands it to a detached child (#424)."""
    from typer.testing import CliRunner

    _git_repo(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.delenv("LORE_CURATOR_MODE", raising=False)
    monkeypatch.setenv("LORE_ROOT", str(tmp_path / "no-vault"))
    inline: list[Path] = []
    monkeypatch.setattr(cm, "generate", lambda root, **kw: inline.append(root))
    spawned: list[Path] = []
    monkeypatch.setattr(hooks, "_spawn_codemap_refresh", lambda cwd: spawned.append(cwd))

    result = CliRunner().invoke(
        hooks.hook_app, ["session-start", "--plain", "--cwd", str(tmp_path)]
    )

    assert result.exit_code == 0, result.output
    assert inline == []
    assert spawned == [tmp_path.resolve()]


def test_codemap_refresh_hook_writes_the_map(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    _git_repo(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")

    result = CliRunner().invoke(hooks.hook_app, ["codemap-refresh", "--cwd", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert (tmp_path / cm.MAP_FILENAME).exists()


def test_codemap_refresh_hook_skips_while_another_refresh_runs(tmp_path: Path, monkeypatch) -> None:
    from lore_core.lockfile import flocked
    from typer.testing import CliRunner

    monkeypatch.setenv("LORE_CACHE", str(tmp_path / "cache"))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_repo(repo)
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")

    with flocked(hooks._codemap_lock_path(repo), blocking=False) as held:
        assert held
        result = CliRunner().invoke(hooks.hook_app, ["codemap-refresh", "--cwd", str(repo)])

    assert result.exit_code == 0, result.output
    assert not (repo / cm.MAP_FILENAME).exists()
