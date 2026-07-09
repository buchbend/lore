"""Tests for the shared-vault consent gate on `lore attach`.

Routing a scope to a wiki with a git remote is an explicit opt-in: the
gate fires the moment ``manual``/``accept`` would write an attachment
row, refusing non-interactive callers that omit ``--confirm-shared``
and prompting interactive ones. A wiki with no remote (solo/local) is
never gated — nothing to consent to.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from lore_cli.__main__ import app
from lore_core.state.attachments import AttachmentsFile
from typer.testing import CliRunner

runner = CliRunner()


# ---------------------------------------------------------------------------
# Bare-repo + clone fixture (same pattern as tests/test_git_sync.py)
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=check,
    )


def _init_bare(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "--bare", "--initial-branch=main")


def _init_clone(origin: Path, dest: Path, name: str = "alice") -> None:
    _git(dest.parent, "clone", str(origin), str(dest))
    _git(dest, "config", "user.email", f"{name}@example.com")
    _git(dest, "config", "user.name", name)


def _seed_first_commit(host: Path) -> None:
    (host / "README.md").write_text("seed\n")
    _git(host, "add", "README.md")
    _git(host, "commit", "-m", "initial")
    _git(host, "push", "-u", "origin", "main")


@pytest.fixture
def lore_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """One shared wiki (real git remote) + one private wiki (no `.git`)."""
    lore_root = tmp_path / "lore-root"
    lore_root.mkdir()
    (lore_root / ".lore").mkdir()
    (lore_root / "wiki").mkdir()
    (lore_root / "wiki" / "private").mkdir()

    origin = tmp_path / "shared-origin.git"
    _init_bare(origin)
    shared = lore_root / "wiki" / "shared"
    _init_clone(origin, shared)
    _seed_first_commit(shared)

    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    monkeypatch.setattr("lore_core.config.get_lore_root", lambda: lore_root)
    monkeypatch.setattr("lore_core.config.get_wiki_root", lambda: lore_root / "wiki")
    return lore_root


def _attached_wikis(lore_root: Path) -> list[str]:
    af = AttachmentsFile(lore_root)
    af.load()
    return [row.wiki for row in af.all()]


# ---- non-interactive (CliRunner default stdin is not a tty) ----


def test_manual_shared_without_flag_refused(lore_env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    result = runner.invoke(
        app,
        ["attach", "manual", "--wiki", "shared", "--scope", "shared:x", "--cwd", str(repo)],
    )
    assert result.exit_code != 0
    assert "shared" in result.output.lower()
    assert "--confirm-shared" in result.output
    assert _attached_wikis(lore_env) == []


def test_manual_shared_with_flag_succeeds(lore_env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    result = runner.invoke(
        app,
        [
            "attach",
            "manual",
            "--wiki",
            "shared",
            "--scope",
            "shared:x",
            "--cwd",
            str(repo),
            "--confirm-shared",
        ],
    )
    assert result.exit_code == 0, result.output
    assert _attached_wikis(lore_env) == ["shared"]


def test_manual_private_wiki_unaffected_by_gate(lore_env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    result = runner.invoke(
        app,
        ["attach", "manual", "--wiki", "private", "--scope", "private:x", "--cwd", str(repo)],
    )
    assert result.exit_code == 0, result.output
    assert _attached_wikis(lore_env) == ["private"]


# ---- interactive ----


def test_manual_shared_interactive_decline_aborts(
    lore_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("lore_cli.attach_cmd._is_interactive", lambda: True)
    repo = tmp_path / "repo"
    repo.mkdir()
    result = runner.invoke(
        app,
        ["attach", "manual", "--wiki", "shared", "--scope", "shared:x", "--cwd", str(repo)],
        input="n\n",
    )
    assert result.exit_code != 0
    assert _attached_wikis(lore_env) == []


def test_manual_shared_interactive_accept_proceeds(
    lore_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("lore_cli.attach_cmd._is_interactive", lambda: True)
    repo = tmp_path / "repo"
    repo.mkdir()
    result = runner.invoke(
        app,
        ["attach", "manual", "--wiki", "shared", "--scope", "shared:x", "--cwd", str(repo)],
        input="y\n",
    )
    assert result.exit_code == 0, result.output
    assert _attached_wikis(lore_env) == ["shared"]
