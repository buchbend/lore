"""Tests for lore_core.git_sync — auto_pull / auto_push / LLM-merge.

Uses a bare-repo + two-clone fixture pattern: ``origin.git`` is a bare
repo; ``host_a`` and ``host_b`` are two clones representing two
machines syncing through it. Tests drive both clones through commits +
pulls/pushes and assert convergence + conflict resolution.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from lore_core.git_sync import (
    ConflictKind,
    SyncStatus,
    _classify_conflict_path,
    auto_pull,
    auto_push,
)

# ---------------------------------------------------------------------------
# Bare-repo + two-clone fixture
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


def _commit_file(host: Path, relpath: str, body: str, message: str) -> None:
    p = host / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    _git(host, "add", relpath)
    _git(host, "commit", "-m", message)


def _seed_first_commit(host: Path) -> None:
    """Bare repos need a first commit before push works."""
    _commit_file(host, "README.md", "# wiki\n", "initial")
    _git(host, "push", "-u", "origin", "main")


@pytest.fixture
def two_hosts(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Returns (origin_bare, host_a, host_b) with main branch initialised."""
    origin = tmp_path / "origin.git"
    host_a = tmp_path / "host_a"
    host_b = tmp_path / "host_b"
    _init_bare(origin)
    _init_clone(origin, host_a, name="alice")
    _seed_first_commit(host_a)
    _init_clone(origin, host_b, name="bob")
    return origin, host_a, host_b


# ---------------------------------------------------------------------------
# auto_pull
# ---------------------------------------------------------------------------


def test_auto_pull_no_git(tmp_path: Path) -> None:
    result = auto_pull(tmp_path / "not-a-repo")
    assert result.status is SyncStatus.SKIPPED_NO_REMOTE


def test_auto_pull_no_remote(tmp_path: Path) -> None:
    repo = tmp_path / "solo"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=main")
    _git(repo, "config", "user.email", "x@example.com")
    _git(repo, "config", "user.name", "x")
    _commit_file(repo, "README.md", "x\n", "initial")

    result = auto_pull(repo)
    assert result.status is SyncStatus.SKIPPED_NO_REMOTE


def test_auto_pull_already_in_sync(two_hosts) -> None:
    _, host_a, _ = two_hosts
    result = auto_pull(host_a)
    assert result.status is SyncStatus.NOOP


def test_auto_pull_fast_forwards_when_remote_ahead(two_hosts) -> None:
    _, host_a, host_b = two_hosts
    _commit_file(host_a, "concepts/foo.md", "---\ntype: concept\n---\n# foo\n", "add foo")
    _git(host_a, "push")

    result = auto_pull(host_b)
    assert result.status is SyncStatus.OK
    assert result.pulled_commits == 1
    assert (host_b / "concepts" / "foo.md").exists()


def test_auto_pull_skipped_when_tree_dirty(two_hosts) -> None:
    _, host_a, _ = two_hosts
    (host_a / "dirty.md").write_text("uncommitted\n")
    result = auto_pull(host_a)
    assert result.status is SyncStatus.SKIPPED_DIRTY


def test_auto_pull_skipped_when_diverged(two_hosts) -> None:
    _, host_a, host_b = two_hosts
    # Both hosts add a unique commit that doesn't conflict in content,
    # but rev histories diverge.
    _commit_file(host_a, "a.md", "a\n", "from a")
    _git(host_a, "push")
    _commit_file(host_b, "b.md", "b\n", "from b")

    result = auto_pull(host_b)
    assert result.status is SyncStatus.SKIPPED_DIVERGED


def test_auto_pull_unreachable_remote_recovers_on_next_sync(two_hosts) -> None:
    _, host_a, host_b = two_hosts
    _commit_file(host_a, "concepts/foo.md", "---\ntype: concept\n---\n# foo\n", "add foo")
    _git(host_a, "push")

    # Remote goes unreachable (e.g. offline host, VPN down).
    _git(host_b, "remote", "set-url", "origin", str(host_b.parent / "nowhere.git"))
    result = auto_pull(host_b)
    assert result.status is SyncStatus.SKIPPED_UNREACHABLE

    # Remote comes back — next sync recovers without any human input.
    _git(host_b, "remote", "set-url", "origin", str(two_hosts[0]))
    result2 = auto_pull(host_b)
    assert result2.status is SyncStatus.OK
    assert (host_b / "concepts" / "foo.md").exists()


# ---------------------------------------------------------------------------
# auto_push (clean paths)
# ---------------------------------------------------------------------------


def test_auto_push_noop_when_in_sync(two_hosts) -> None:
    _, host_a, _ = two_hosts
    result = auto_push(host_a)
    assert result.status is SyncStatus.NOOP


def test_auto_push_pushes_local_commit(two_hosts) -> None:
    _, host_a, host_b = two_hosts
    _commit_file(host_a, "concepts/foo.md", "---\ntype: concept\n---\n# foo\n", "add foo")

    result = auto_push(host_a)
    assert result.status is SyncStatus.OK
    assert result.pushed_commits == 1

    # Host B can pull it.
    pull_b = auto_pull(host_b)
    assert pull_b.status is SyncStatus.OK
    assert (host_b / "concepts" / "foo.md").exists()


def test_auto_push_unreachable_remote_queues_locally_and_recovers_on_next_sync(
    two_hosts,
) -> None:
    origin, host_a, host_b = two_hosts
    _commit_file(host_a, "concepts/foo.md", "---\ntype: concept\n---\n# foo\n", "add foo")

    # Remote goes unreachable (e.g. offline host, VPN down).
    _git(host_a, "remote", "set-url", "origin", str(host_a.parent / "nowhere.git"))
    result = auto_push(host_a)
    assert result.status is SyncStatus.SKIPPED_UNREACHABLE

    # Nothing was lost — the commit is still queued locally.
    log = _git(host_a, "log", "--oneline", "-1").stdout
    assert "add foo" in log

    # Remote comes back — next sync recovers without any human input.
    _git(host_a, "remote", "set-url", "origin", str(origin))
    result2 = auto_push(host_a)
    assert result2.status is SyncStatus.OK
    assert result2.pushed_commits == 1

    pull_b = auto_pull(host_b)
    assert pull_b.status is SyncStatus.OK
    assert (host_b / "concepts" / "foo.md").exists()


# ---------------------------------------------------------------------------
# auto_push (conflict paths)
# ---------------------------------------------------------------------------


class _StubLlmMessages:
    def __init__(self, response_text: str) -> None:
        self._text = response_text
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)

        class _Block:
            type = "text"

            def __init__(self, text):
                self.text = text

        class _Resp:
            def __init__(self, text):
                self.content = [_Block(text)]

        return _Resp(self._text)


class StubLlm:
    """Anthropic-shape stub: ``llm.messages.create(...)`` returns canned text."""

    def __init__(self, response_text: str) -> None:
        self.messages = _StubLlmMessages(response_text)


def test_auto_push_resolves_surface_conflict_via_llm(two_hosts) -> None:
    _, host_a, host_b = two_hosts

    # Both hosts independently create concepts/foo.md with overlapping content.
    _commit_file(
        host_a,
        "concepts/foo.md",
        "---\ntype: concept\n---\n# foo\n\nFact A.\n",
        "host_a adds foo",
    )
    _git(host_a, "push")

    _commit_file(
        host_b,
        "concepts/foo.md",
        "---\ntype: concept\n---\n# foo\n\nFact B.\n",
        "host_b adds foo",
    )

    merged_body = "---\ntype: concept\n---\n# foo\n\nFact A. Fact B.\n"
    result = auto_push(
        host_b,
        llm_client=StubLlm(merged_body),
        surface_dirs=["concepts"],
    )
    assert result.status is SyncStatus.MERGED, f"got {result}"
    assert "concepts/foo.md" in result.merged_paths
    assert (host_b / "concepts" / "foo.md").read_text() == merged_body

    # Crucially: no per-host artefact files.
    foo_dir = host_b / "concepts"
    assert sorted(p.name for p in foo_dir.iterdir()) == ["foo.md"]


def test_auto_push_blocks_when_no_llm_client_provided(two_hosts) -> None:
    _, host_a, host_b = two_hosts
    _commit_file(host_a, "concepts/foo.md", "---\ntype: concept\n---\nA\n", "a")
    _git(host_a, "push")
    _commit_file(host_b, "concepts/foo.md", "---\ntype: concept\n---\nB\n", "b")

    result = auto_push(host_b, llm_client=None, surface_dirs=["concepts"])
    assert result.status is SyncStatus.MERGE_BLOCKED
    assert "concepts/foo.md" in result.blocked_paths
    # Tree should be back to clean — abort completed.
    porcelain = _git(host_b, "status", "--porcelain").stdout.strip()
    assert porcelain == "", f"working tree not clean after abort: {porcelain!r}"


def test_auto_push_picks_ours_for_regenerable_artifacts(two_hosts) -> None:
    _, host_a, host_b = two_hosts
    _commit_file(host_a, "_catalog.json", '{"a": 1}\n', "a catalog")
    _git(host_a, "push")
    _commit_file(host_b, "_catalog.json", '{"b": 2}\n', "b catalog")

    result = auto_push(host_b, llm_client=None, surface_dirs=["concepts"])
    assert result.status is SyncStatus.MERGED, f"got {result}"
    assert "_catalog.json" in result.merged_paths
    # Ours wins.
    assert (host_b / "_catalog.json").read_text() == '{"b": 2}\n'


def test_auto_push_blocks_unknown_conflict_path(two_hosts) -> None:
    _, host_a, host_b = two_hosts
    _commit_file(host_a, "CLAUDE.md", "from a\n", "a")
    _git(host_a, "push")
    _commit_file(host_b, "CLAUDE.md", "from b\n", "b")

    result = auto_push(host_b, llm_client=None, surface_dirs=["concepts"])
    assert result.status is SyncStatus.MERGE_BLOCKED
    assert "CLAUDE.md" in result.blocked_paths


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# is_diverged — public probe for `lore status`
# ---------------------------------------------------------------------------


def test_is_diverged_false_when_in_sync(two_hosts) -> None:
    from lore_core.git_sync import is_diverged

    _, host_a, _ = two_hosts
    assert is_diverged(host_a) is False


def test_is_diverged_false_when_only_local_ahead(two_hosts) -> None:
    """Local-only commits (clean push path) are not "diverged"."""
    from lore_core.git_sync import is_diverged

    _, host_a, _ = two_hosts
    _commit_file(host_a, "a.md", "a\n", "from a")
    assert is_diverged(host_a) is False


def test_is_diverged_true_when_both_sides_have_unique_commits(two_hosts) -> None:
    from lore_core.git_sync import is_diverged

    _, host_a, host_b = two_hosts
    _commit_file(host_a, "a.md", "a\n", "from a")
    _git(host_a, "push")
    _commit_file(host_b, "b.md", "b\n", "from b")
    # Host B fetches but doesn't pull — both sides ahead.
    _git(host_b, "fetch")
    assert is_diverged(host_b) is True


def test_is_diverged_false_for_non_repo(tmp_path: Path) -> None:
    from lore_core.git_sync import is_diverged

    assert is_diverged(tmp_path / "not-a-repo") is False


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,expected",
    [
        ("concepts/foo.md", ConflictKind.SURFACE),
        ("decisions/2026-04-26-pivot.md", ConflictKind.SURFACE),
        ("sessions/alice/2026/04/26-foo.md", ConflictKind.SESSION),
        ("_catalog.json", ConflictKind.REGENERABLE),
        ("_threads.txt", ConflictKind.REGENERABLE),
        ("_concepts.txt", ConflictKind.REGENERABLE),
        ("_decisions.txt", ConflictKind.REGENERABLE),
        ("_recent.txt", ConflictKind.REGENERABLE),
        ("_index.txt", ConflictKind.REGENERABLE),
        # Legacy filenames still classified as regenerable during the
        # rollout window — see _REGENERABLE_FILENAMES in git_sync.py.
        ("threads.md", ConflictKind.REGENERABLE),
        ("_recent.md", ConflictKind.REGENERABLE),
        ("llms.txt", ConflictKind.REGENERABLE),
        ("_index.md", ConflictKind.REGENERABLE),
        ("CLAUDE.md", ConflictKind.UNKNOWN),
        ("misc/foo.md", ConflictKind.UNKNOWN),
    ],
)
def test_classify_conflict_path(path: str, expected: ConflictKind) -> None:
    assert _classify_conflict_path(path, ["concepts", "decisions"]) is expected
