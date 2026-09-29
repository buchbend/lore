"""Wiki auto-pull runs off the SessionStart critical path (#424).

The fetch needs the network, so the hook spawns it in the background and
the banner shows the warning the previous background pull recorded.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from lore_core.session_start import record_auto_pull, recorded_auto_pull_warning


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _wiki_with_remote(lore_root: Path, name: str = "demo") -> Path:
    wiki = lore_root / "wiki" / name
    wiki.mkdir(parents=True)
    (wiki / "a.md").write_text("a\n")
    remote = lore_root.parent / f"{name}-remote.git"
    _git(lore_root.parent, "init", "-q", "--bare", "-b", "main", str(remote))
    _git(wiki, "init", "-q", "-b", "main")
    _git(wiki, "add", "-A")
    _git(wiki, "commit", "-qm", "init")
    _git(wiki, "remote", "add", "origin", str(remote))
    _git(wiki, "push", "-q", "-u", "origin", "main")
    return wiki


def test_no_recorded_pull_means_no_warning(tmp_path: Path) -> None:
    assert recorded_auto_pull_warning(tmp_path, "demo") is None


def test_recorded_dirty_pull_surfaces_warning(tmp_path: Path) -> None:
    lore_root = tmp_path / "vault"
    wiki = _wiki_with_remote(lore_root)
    (wiki / "a.md").write_text("edited\n")

    record_auto_pull(lore_root, "demo")

    warning = recorded_auto_pull_warning(lore_root, "demo")
    assert warning is not None
    assert "uncommitted changes" in warning


def test_clean_pull_clears_an_earlier_warning(tmp_path: Path) -> None:
    lore_root = tmp_path / "vault"
    wiki = _wiki_with_remote(lore_root)
    (wiki / "a.md").write_text("edited\n")
    record_auto_pull(lore_root, "demo")
    _git(wiki, "checkout", "--", "a.md")

    record_auto_pull(lore_root, "demo")

    assert recorded_auto_pull_warning(lore_root, "demo") is None


def test_a_pull_that_raises_clears_an_earlier_warning(tmp_path: Path, monkeypatch) -> None:
    lore_root = tmp_path / "vault"
    wiki = _wiki_with_remote(lore_root)
    (wiki / "a.md").write_text("edited\n")
    record_auto_pull(lore_root, "demo")
    assert recorded_auto_pull_warning(lore_root, "demo") is not None

    def boom(*_args, **_kwargs):
        raise RuntimeError("pull blew up")

    monkeypatch.setattr("lore_core.session_start._auto_pull_wiki", boom)
    with pytest.raises(RuntimeError):
        record_auto_pull(lore_root, "demo")

    assert recorded_auto_pull_warning(lore_root, "demo") is None


def test_clean_pull_fast_forwards_the_wiki(tmp_path: Path) -> None:
    lore_root = tmp_path / "vault"
    wiki = _wiki_with_remote(lore_root)
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(tmp_path / "demo-remote.git"), str(other))
    (other / "b.md").write_text("b\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-qm", "b")
    _git(other, "push", "-q")

    record_auto_pull(lore_root, "demo")

    assert (wiki / "b.md").exists()


def _attach(lore_root: Path, project: Path, wiki: str = "demo") -> None:
    from datetime import UTC, datetime

    from lore_core.state.attachments import Attachment, AttachmentsFile

    project.mkdir(parents=True, exist_ok=True)
    af = AttachmentsFile(lore_root)
    af.load()
    af.add(
        Attachment(
            path=project,
            wiki=wiki,
            scope=f"{wiki}:proj",
            attached_at=datetime.now(UTC),
            source="manual",
        )
    )
    af.save()


def test_session_start_shows_recorded_warning_and_pulls_in_background(
    tmp_path: Path, monkeypatch
) -> None:
    from lore_cli import hooks
    from lore_core import git_sync
    from typer.testing import CliRunner

    lore_root = tmp_path / "vault"
    wiki = _wiki_with_remote(lore_root)
    (wiki / "a.md").write_text("edited\n")
    record_auto_pull(lore_root, "demo")
    project = tmp_path / "project"
    _attach(lore_root, project)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    monkeypatch.delenv("LORE_CURATOR_MODE", raising=False)
    inline_pulls: list[Path] = []
    monkeypatch.setattr(git_sync, "auto_pull", lambda d: inline_pulls.append(d))
    spawned: list[str] = []
    monkeypatch.setattr(
        hooks, "_spawn_detached_wiki_pull", lambda root, name: spawned.append(name) or True
    )

    result = CliRunner().invoke(hooks.hook_app, ["session-start", "--plain", "--cwd", str(project)])

    assert result.exit_code == 0, result.output
    assert "uncommitted changes" in result.output
    assert inline_pulls == []
    assert spawned == ["demo"]


def test_wiki_pull_hook_records_the_pull(tmp_path: Path, monkeypatch) -> None:
    from lore_cli import hooks
    from typer.testing import CliRunner

    lore_root = tmp_path / "vault"
    wiki = _wiki_with_remote(lore_root)
    (wiki / "a.md").write_text("edited\n")
    monkeypatch.setenv("LORE_ROOT", str(lore_root))

    result = CliRunner().invoke(hooks.hook_app, ["wiki-pull", "--wiki", "demo"])

    assert result.exit_code == 0, result.output
    assert "uncommitted changes" in (recorded_auto_pull_warning(lore_root, "demo") or "")
