"""`lore wiki commit <entry>`: check, commit and push one wiki entry (issue #601)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from lore_cli.__main__ import app
from typer.testing import CliRunner

runner = CliRunner()

GOOD = "---\ndescription: A short entry.\n---\nA short body.\n"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def wiki(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "tester")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "tester@example.com")
    monkeypatch.setattr("lore_core.wiki_check.shutil.which", lambda _: None)
    w = tmp_path / "wiki" / "demo"
    w.mkdir(parents=True)
    return w


def _write(wiki: Path, rel: str, text: str = GOOD) -> Path:
    path = wiki / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _commit(entry: Path):
    return runner.invoke(app, ["wiki", "commit", str(entry)])


def _files_of(repo: Path, rev: str) -> list[str]:
    return _git(repo, "show", "--name-only", "--format=", rev).splitlines()


@pytest.fixture
def remote(wiki: Path, tmp_path: Path) -> Path:
    """A bare remote; the wiki is a clone of it with one pushed commit."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "--initial-branch=main", str(origin))
    _git(wiki, "init", "--initial-branch=main")
    _write(wiki, "README.md", "# demo\n")
    _git(wiki, "add", "-A")
    _git(wiki, "commit", "-m", "seed")
    _git(wiki, "remote", "add", "origin", str(origin))
    _git(wiki, "push", "-u", "origin", "main")
    return origin


def _push_from_other_clone(origin: Path, tmp_path: Path, rel: str, text: str) -> None:
    other = tmp_path / "other"
    _git(tmp_path, "clone", str(origin), str(other))
    _write(other, rel, text)
    _git(other, "add", "-A")
    _git(other, "commit", "-m", "other host")
    _git(other, "push")


def test_a_wiki_without_git_gets_init_and_the_entry_commit(wiki: Path) -> None:
    entry = _write(wiki, "research/topic.md")
    result = _commit(entry)
    assert result.exit_code == 0, result.output
    head = _git(wiki, "rev-parse", "--short", "HEAD")
    assert str(entry) in result.output
    assert head in result.output
    assert _files_of(wiki, "HEAD") == ["research/topic.md"]
    assert "error" not in result.output.lower()


def test_an_error_in_the_entry_blocks_the_commit(wiki: Path) -> None:
    entry = _write(wiki, "research/topic.md", "no frontmatter\n")
    result = _commit(entry)
    assert result.exit_code == 1
    assert "missing_description" in result.output
    assert not (wiki / ".git").exists()


def test_an_error_in_another_note_does_not_block(wiki: Path) -> None:
    _git(wiki, "init")
    _write(wiki, "concepts/broken.md", "---\ndescription: d\n---\n[[ghost]]\n")
    entry = _write(wiki, "research/topic.md")
    result = _commit(entry)
    assert result.exit_code == 0, result.output
    assert "research/topic.md" in _files_of(wiki, "HEAD")


def test_hand_edits_land_in_their_own_commit_before_the_entry(wiki: Path) -> None:
    _git(wiki, "init")
    _write(wiki, "notebook/scratch.md")
    _write(wiki, ".transcripts/session.jsonl", "{}\n")
    entry = _write(wiki, "research/topic.md")
    result = _commit(entry)
    assert result.exit_code == 0, result.output
    assert _files_of(wiki, "HEAD") == ["research/topic.md"]
    assert _files_of(wiki, "HEAD~1") == ["notebook/scratch.md"]
    assert _git(wiki, "status", "--porcelain") == "?? .transcripts/"


def test_remote_changes_merge_first_and_the_entry_is_pushed(
    wiki: Path, remote: Path, tmp_path: Path
) -> None:
    _push_from_other_clone(remote, tmp_path, "concepts/theirs.md", GOOD)
    entry = _write(wiki, "research/topic.md")
    result = _commit(entry)
    assert result.exit_code == 0, result.output
    assert (wiki / "concepts" / "theirs.md").exists()
    assert _files_of(wiki, "HEAD") == ["research/topic.md"]
    assert _git(remote, "rev-parse", "main") == _git(wiki, "rev-parse", "HEAD")


def test_a_remote_without_upstream_gets_the_first_push(wiki: Path, tmp_path: Path) -> None:
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "--initial-branch=main", str(origin))
    _git(wiki, "init", "--initial-branch=main")
    _git(wiki, "remote", "add", "origin", str(origin))
    entry = _write(wiki, "research/topic.md")
    result = _commit(entry)
    assert result.exit_code == 0, result.output
    assert _git(origin, "rev-parse", "main") == _git(wiki, "rev-parse", "HEAD")


def test_a_merge_conflict_keeps_the_local_commit_and_names_the_path(
    wiki: Path, remote: Path, tmp_path: Path
) -> None:
    _push_from_other_clone(remote, tmp_path, "README.md", "# theirs\n")
    _write(wiki, "README.md", "# mine\n")
    entry = _write(wiki, "research/topic.md")
    result = _commit(entry)
    assert result.exit_code == 1
    assert "README.md" in result.output
    assert _files_of(wiki, "HEAD") == ["research/topic.md"]
    assert _files_of(wiki, "HEAD~1") == ["README.md"]
    assert not (wiki / ".git" / "MERGE_HEAD").exists()
    assert _git(remote, "rev-parse", "main") != _git(wiki, "rev-parse", "HEAD")


def test_an_extended_entry_merges_with_a_remote_change_to_it(
    wiki: Path, remote: Path, tmp_path: Path
) -> None:
    body = GOOD + "".join(f"Line {n}.\n" for n in range(20))
    _write(wiki, "research/topic.md", body)
    _git(wiki, "add", "-A")
    _git(wiki, "commit", "-m", "entry")
    _git(wiki, "push")
    _push_from_other_clone(remote, tmp_path, "research/topic.md", body + "Their line.\n")
    entry = _write(wiki, "research/topic.md", body.replace("Line 1.", "My line."))
    result = _commit(entry)
    assert result.exit_code == 0, result.output
    text = entry.read_text()
    assert "My line." in text and "Their line." in text
    assert _git(remote, "rev-parse", "main") == _git(wiki, "rev-parse", "HEAD")


def test_a_remote_branch_without_upstream_merges_before_the_push(
    wiki: Path, tmp_path: Path
) -> None:
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "--initial-branch=main", str(origin))
    _git(wiki, "init", "--initial-branch=main")
    _write(wiki, "README.md", "# demo\n")
    _git(wiki, "add", "-A")
    _git(wiki, "commit", "-m", "seed")
    _git(wiki, "remote", "add", "origin", str(origin))
    _git(wiki, "push", "origin", "main")  # no upstream set
    _push_from_other_clone(origin, tmp_path, "concepts/theirs.md", GOOD)
    entry = _write(wiki, "research/topic.md")
    result = _commit(entry)
    assert result.exit_code == 0, result.output
    assert (wiki / "concepts" / "theirs.md").exists()
    assert _git(origin, "rev-parse", "main") == _git(wiki, "rev-parse", "HEAD")


def test_a_glob_character_in_the_entry_name_is_literal(wiki: Path) -> None:
    _git(wiki, "init")
    _write(wiki, "research/q1.md")
    entry = _write(wiki, "research/q[1-3].md")
    result = _commit(entry)
    assert result.exit_code == 0, result.output
    assert _files_of(wiki, "HEAD") == ["research/q[1-3].md"]
    assert _files_of(wiki, "HEAD~1") == ["research/q1.md"]


def test_a_wiki_inside_another_repo_is_refused(wiki: Path, tmp_path: Path) -> None:
    _git(tmp_path, "init")
    entry = _write(wiki, "research/topic.md")
    result = _commit(entry)
    assert result.exit_code == 2
    assert "inside the git repo" in result.output
    assert subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path).returncode != 0


def test_an_entry_the_check_does_not_read_is_refused(wiki: Path) -> None:
    entry = _write(wiki, "inbox/topic.md", "no frontmatter\n")
    assert _commit(entry).exit_code == 2
    assert not (wiki / ".git").exists()


def test_an_unreachable_remote_keeps_the_commit_and_exits_1(wiki: Path, remote: Path) -> None:
    import shutil

    shutil.rmtree(remote)
    entry = _write(wiki, "research/topic.md")
    result = _commit(entry)
    assert result.exit_code == 1
    assert "not pushed" in result.output
    assert _files_of(wiki, "HEAD") == ["research/topic.md"]
