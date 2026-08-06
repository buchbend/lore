"""Flag transport — the commit at write time and the push at the boundary.

A flag reaches a teammate only after three parts run: the write, the
commit, the push. These tests drive real git repositories — a bare
origin plus two clones standing for two machines — the way
``tests/test_git_sync.py`` does. A mocked git proves nothing about
transport.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from lore_cli.hooks import hook_app
from lore_core import flag
from lore_core.git_sync import SyncStatus, auto_pull
from lore_core.scope_resolver import resolve_scope
from lore_core.session_start import maybe_auto_push_for_scope
from typer.testing import CliRunner

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures — a vault whose wiki is a clone, and a teammate's clone
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=check,
    )


def _identify(repo: Path, name: str) -> None:
    _git(repo, "config", "user.email", f"{name}@example.com")
    _git(repo, "config", "user.name", name)


def _committed_paths(repo: Path) -> list[str]:
    return _git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split()


def _file_flag(target: str = "concepts/reaper.md") -> flag.FlagWrite:
    return flag.write(
        "The reaper starves.",
        body="Two sessions raced the same lock; the loser never retried.",
        wiki="lore",
        target=target,
        refs=[("pr", "357")],
        transcript="tr-9f2c",
        author="claude",
        now="2026-08-05",
    )


@pytest.fixture()
def team_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Returns ``(vault, wiki, teammate)`` — two clones of one bare origin."""
    vault = tmp_path / "vault"
    monkeypatch.setenv("LORE_ROOT", str(vault))
    monkeypatch.setenv("LORE_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    monkeypatch.delenv("LORE_SUPPRESS_CAPTURE", raising=False)

    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "--initial-branch=main")

    wiki_root = vault / "wiki"
    wiki_root.mkdir(parents=True)
    wiki = wiki_root / "lore"
    _git(wiki_root, "clone", str(origin), str(wiki))
    _identify(wiki, "alice")
    (wiki / "concepts").mkdir()
    (wiki / "README.md").write_text("# lore\n")
    _git(wiki, "add", "README.md")
    _git(wiki, "commit", "-m", "initial")
    _git(wiki, "push", "-u", "origin", "main")

    teammate = tmp_path / "teammate"
    _git(tmp_path, "clone", str(origin), str(teammate))
    _identify(teammate, "bob")
    return vault, wiki, teammate


# ---------------------------------------------------------------------------
# The commit — every flag lands in the wiki's history
# ---------------------------------------------------------------------------


def test_write_commits_the_flag_into_the_wiki(team_vault) -> None:
    _vault, wiki, _teammate = team_vault

    result = _file_flag()

    assert result.status == "written"
    assert _git(wiki, "status", "--porcelain").stdout.strip() == ""
    assert _committed_paths(wiki) == ["concepts/reaper.md"]


def test_write_leaves_a_staged_neighbour_out_of_the_commit(team_vault) -> None:
    """A human's staged work is theirs — the flag commit carries one note."""
    _vault, wiki, _teammate = team_vault
    (wiki / "concepts" / "draft.md").write_text("half-written\n")
    _git(wiki, "add", "concepts/draft.md")

    _file_flag()

    assert _committed_paths(wiki) == ["concepts/reaper.md"]
    assert "concepts/draft.md" in _git(wiki, "status", "--porcelain").stdout


def test_write_lands_in_a_wiki_that_is_not_a_git_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    monkeypatch.setenv("LORE_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    (tmp_path / "wiki" / "lore" / "concepts").mkdir(parents=True)

    result = _file_flag()

    assert result.status == "written"
    assert (tmp_path / "wiki" / "lore" / "concepts" / "reaper.md").exists()


# ---------------------------------------------------------------------------
# The push — the session boundary hands the flag to the teammate
# ---------------------------------------------------------------------------


class _Scope:
    wiki = "lore"
    scope = "proj"


def test_boundary_push_delivers_the_flag_to_a_teammate(team_vault) -> None:
    vault, _wiki, teammate = team_vault
    _file_flag()

    result = maybe_auto_push_for_scope(_Scope(), vault)

    assert result is not None
    assert result.status is SyncStatus.OK
    assert auto_pull(teammate).status is SyncStatus.OK
    assert "The reaper starves." in (teammate / "concepts" / "reaper.md").read_text()


def test_boundary_push_skips_a_wiki_without_a_remote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    monkeypatch.setenv("LORE_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    wiki = tmp_path / "wiki" / "lore"
    (wiki / "concepts").mkdir(parents=True)
    _git(wiki, "init", "--initial-branch=main")
    _identify(wiki, "alice")
    _file_flag()

    # No remote means no transport to run: the config default opts out
    # and lore never reaches git at all.
    assert maybe_auto_push_for_scope(_Scope(), tmp_path) is None


def test_boundary_push_blocks_on_a_note_conflict_and_leaves_the_tree_clean(
    team_vault,
) -> None:
    """No LLM client crosses the boundary, so a note conflict blocks."""
    vault, wiki, teammate = team_vault
    (teammate / "concepts").mkdir(exist_ok=True)
    (teammate / "concepts" / "reaper.md").write_text("---\ntype: concept\n---\n\nBob's version.\n")
    _git(teammate, "add", "concepts/reaper.md")
    _git(teammate, "commit", "-m", "bob files one")
    _git(teammate, "push")
    _file_flag()

    result = maybe_auto_push_for_scope(_Scope(), vault)

    assert result is not None
    assert result.status is SyncStatus.MERGE_BLOCKED
    assert "concepts/reaper.md" in result.blocked_paths
    porcelain = _git(wiki, "status", "--porcelain").stdout.strip()
    assert porcelain == "", f"working tree not clean after abort: {porcelain!r}"


# ---------------------------------------------------------------------------
# The wiring — the session-end hook runs the push
# ---------------------------------------------------------------------------


class _NoTranscriptAdapter:
    integration = "no-transcripts"

    def list_transcripts(self, directory: Path) -> list:  # noqa: ARG002
        return []

    def read_slice_after_hash(self, *a, **kw):
        yield from ()

    def read_slice(self, *a, **kw):
        yield from ()

    def is_complete(self, handle) -> bool:  # noqa: ARG002
        return True


@pytest.fixture()
def registered_adapter():
    from lore_adapters import register
    from lore_adapters.registry import _REGISTRY

    adapter = _NoTranscriptAdapter()
    register(adapter)
    yield adapter
    _REGISTRY.pop(adapter.integration, None)


def test_session_end_hook_pushes_the_wiki(team_vault, registered_adapter, tmp_path: Path) -> None:
    from lore_core.state.attachments import Attachment, AttachmentsFile

    vault, _wiki, teammate = team_vault
    project = tmp_path / "project"
    project.mkdir()
    (vault / ".lore").mkdir(parents=True, exist_ok=True)
    af = AttachmentsFile(vault)
    af.load()
    af.add(
        Attachment(
            path=project,
            wiki="lore",
            scope="proj",
            attached_at=datetime.now(tz=UTC),
            source="manual",
        )
    )
    af.save()
    assert resolve_scope(project) is not None
    _file_flag()

    result = runner.invoke(
        hook_app,
        [
            "capture",
            "--event",
            "session-end",
            "--cwd",
            str(project),
            "--integration",
            registered_adapter.integration,
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert auto_pull(teammate).status is SyncStatus.OK
    assert (teammate / "concepts" / "reaper.md").exists()
