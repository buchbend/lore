"""Tests for lore_curator.session_activity — Phase 3 mechanical collectors.

Each collector returns empty / soft-falsy on missing tooling so Curator A
never blocks on git or gh. Tests exercise the happy path with controlled
fixtures plus the soft-fail behaviour.
"""
from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lore_curator.session_activity import (
    CommitRef,
    collect_issues_in_window,
    collect_projects_for_session,
    extract_issue_refs,
    render_commits_section,
    render_issue_section,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _init_repo(repo_root: Path, *, remote_url: str | None = None) -> None:
    """Initialise a git repo at ``repo_root`` with author config."""
    repo_root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo_root, check=True)
    if remote_url:
        subprocess.run(
            ["git", "remote", "add", "origin", remote_url],
            cwd=repo_root, check=True,
        )


def _commit(
    repo_root: Path, *, subject: str, content: str = "x", filename: str = "f.txt",
    when: datetime | None = None,
) -> None:
    """Make one commit. Optionally backdate via GIT_*_DATE env vars."""
    (repo_root / filename).write_text(content)
    subprocess.run(["git", "add", "-A"], cwd=repo_root, check=True)
    env = None
    if when is not None:
        iso = when.isoformat()
        env = {"GIT_AUTHOR_DATE": iso, "GIT_COMMITTER_DATE": iso}
    subprocess.run(
        ["git", "commit", "-q", "-m", subject, "--no-verify"],
        cwd=repo_root, check=True,
        env={**__import__("os").environ, **(env or {})} if env else None,
    )


# ---------------------------------------------------------------------------
# collect_commits_by_sha — SHA-bound resolver (Step 2)
# ---------------------------------------------------------------------------


def _resolve_sha(repo_root: Path, ref: str = "HEAD") -> str:
    """Get the SHA for a ref; helper for resolver tests."""
    out = subprocess.run(
        ["git", "rev-parse", ref],
        cwd=repo_root, capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def test_resolver_t_empty_sha_list(tmp_path):
    """Empty SHAs MUST short-circuit — bare `git show` defaults to HEAD."""
    from lore_curator.session_activity import collect_commits_by_sha

    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, subject="should-not-appear")
    # If the resolver naively passes [] to git show, git shows HEAD and
    # the list comes back non-empty. This is the architect-flagged
    # silent regression we MUST guard.
    assert collect_commits_by_sha(repo, []) == []


def test_resolver_t_repo_root_none():
    from lore_curator.session_activity import collect_commits_by_sha
    assert collect_commits_by_sha(None, ["abc1234"]) == []


def test_resolver_t_repo_root_does_not_exist(tmp_path):
    from lore_curator.session_activity import collect_commits_by_sha
    assert collect_commits_by_sha(tmp_path / "nope", ["abc1234"]) == []


def test_resolver_t_single_resolvable(tmp_path):
    from lore_curator.session_activity import collect_commits_by_sha

    repo = tmp_path / "repo"
    _init_repo(repo, remote_url="https://github.com/test/repo.git")
    _commit(repo, subject="add ledger")
    sha = _resolve_sha(repo)

    commits = collect_commits_by_sha(repo, [sha])
    assert len(commits) == 1
    c = commits[0]
    assert sha.startswith(c.short_hash)
    assert c.subject == "add ledger"
    assert c.branch == "main"
    assert c.repo == "test/repo"


def test_resolver_t_all_unresolvable(tmp_path):
    from lore_curator.session_activity import collect_commits_by_sha

    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, subject="x")

    # 7-char hex strings unlikely to exist as real SHAs.
    assert collect_commits_by_sha(repo, ["deadbe0", "deadbe1", "deadbe2"]) == []


def test_resolver_t_mixed_resolvable_unresolvable(tmp_path):
    from lore_curator.session_activity import collect_commits_by_sha

    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, subject="real one")
    real = _resolve_sha(repo)

    commits = collect_commits_by_sha(repo, ["badf00d", real, "deadbe1"])
    assert len(commits) == 1
    assert commits[0].subject == "real one"


def test_resolver_t_short_sha_resolves(tmp_path):
    from lore_curator.session_activity import collect_commits_by_sha

    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, subject="prefix test")
    full = _resolve_sha(repo)

    commits = collect_commits_by_sha(repo, [full[:7]])
    assert len(commits) == 1
    assert commits[0].subject == "prefix test"


def test_resolver_t_subject_with_tab(tmp_path):
    """Commit subject containing a literal tab — must round-trip without
    field-separator confusion."""
    from lore_curator.session_activity import collect_commits_by_sha

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "f.txt").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "subj with\ttab", "--no-verify"],
        cwd=repo, check=True,
    )
    sha = _resolve_sha(repo)

    commits = collect_commits_by_sha(repo, [sha])
    assert len(commits) == 1
    assert commits[0].subject == "subj with\ttab"


# ---------------------------------------------------------------------------
# extract_issue_refs
# ---------------------------------------------------------------------------


def test_extract_issue_refs_classifies_open_vs_close():
    text = "We opened #42 today. Then closed #29. Also fixes #7."
    opened, closed = extract_issue_refs(text)
    assert opened == {42}
    assert closed == {29, 7}


def test_extract_issue_refs_dedupes():
    text = "Closes #29 then closes #29 again"
    opened, closed = extract_issue_refs(text)
    assert opened == set()
    assert closed == {29}


def test_extract_issue_refs_handles_squash_merge_subjects():
    text = "Merge PR: closes #29, fixes #30, resolves #31"
    opened, closed = extract_issue_refs(text)
    assert closed == {29, 30, 31}


def test_extract_issue_refs_ignores_bare_hash_numbers():
    """A bare ``#42`` without an action verb does NOT count.

    Otherwise every reference in a commit body or turn text would
    falsely promote an unrelated issue."""
    text = "See related #42 for context. We worked on something else."
    opened, closed = extract_issue_refs(text)
    assert opened == set()
    assert closed == set()


def test_extract_issue_refs_empty_text_safe():
    assert extract_issue_refs("") == (set(), set())
    assert extract_issue_refs("") == (set(), set())


# ---------------------------------------------------------------------------
# collect_issues_in_window
# ---------------------------------------------------------------------------


def test_collect_issues_intersects_referenced_with_gh_state(monkeypatch):
    """Only issues actually referenced this session land in the section.

    The "fetch all open / all closed" calls return everything; the
    intersection drops issues that weren't mentioned in commits or
    turn text.
    """
    fake_open = [
        {"number": 42, "title": "Open issue A", "state": "OPEN"},
        {"number": 50, "title": "Unrelated open", "state": "OPEN"},
    ]
    fake_closed = [
        {"number": 29, "title": "Closed issue B", "state": "CLOSED"},
        {"number": 60, "title": "Unrelated closed", "state": "CLOSED"},
    ]

    def fake_gh_issues(repo, filter_str):
        if "open" in filter_str:
            return fake_open
        return fake_closed

    monkeypatch.setattr("lore_curator.session_activity.gh_issues", fake_gh_issues)

    opened, closed = collect_issues_in_window(
        "test/repo",
        referenced_opened={42},
        referenced_closed={29},
    )
    assert [i["number"] for i in opened] == [42]
    assert [i["number"] for i in closed] == [29]


def test_collect_issues_returns_empty_when_no_repo():
    opened, closed = collect_issues_in_window(
        "", referenced_opened={42}, referenced_closed={29},
    )
    assert opened == []
    assert closed == []


def test_collect_issues_returns_empty_when_no_references(monkeypatch):
    """No referenced issues → no gh call, no results."""
    called = []

    def fake_gh_issues(repo, filter_str):
        called.append((repo, filter_str))
        return []

    monkeypatch.setattr("lore_curator.session_activity.gh_issues", fake_gh_issues)
    opened, closed = collect_issues_in_window(
        "test/repo", referenced_opened=set(), referenced_closed=set(),
    )
    assert opened == []
    assert closed == []
    assert called == []  # no gh call when nothing to look up


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def test_render_commits_section_includes_repo_and_branch():
    commits = [
        CommitRef(short_hash="abc1234", subject="add ledger",
                  branch="feature/x", repo="org/lore"),
    ]
    lines = render_commits_section(commits)
    assert lines == ["- `abc1234` add ledger (org/lore/feature/x)"]


def test_render_commits_section_omits_location_when_repo_missing():
    commits = [CommitRef(short_hash="abc1234", subject="add ledger",
                         branch="main", repo="")]
    lines = render_commits_section(commits)
    assert lines == ["- `abc1234` add ledger (main)"]


def test_render_issue_section_includes_repo_suffix():
    issues = [{"number": 29, "title": "fix the thing", "state": "CLOSED"}]
    lines = render_issue_section(issues, repo="org/lore")
    assert lines == ["- #29 fix the thing (org/lore)"]


# ---------------------------------------------------------------------------
# collect_projects_for_session
# ---------------------------------------------------------------------------


def test_collect_projects_picks_up_cwd_repo_when_project_note_exists(tmp_path, monkeypatch):
    wiki = tmp_path / "wiki"
    (wiki / "projects").mkdir(parents=True)
    (wiki / "projects" / "lore.md").write_text("---\ntype: project\n---\n")

    monkeypatch.setattr(
        "lore_curator.session_activity.current_repo",
        lambda cwd: "buchbend/lore",
    )

    refs = collect_projects_for_session(
        cwd=tmp_path, files_touched=[], wiki_root=wiki,
    )
    assert refs == ["lore"]


def test_collect_projects_drops_repo_with_no_project_note(tmp_path, monkeypatch):
    wiki = tmp_path / "wiki"
    (wiki / "projects").mkdir(parents=True)
    # No project note for the repo — should be dropped silently.

    monkeypatch.setattr(
        "lore_curator.session_activity.current_repo",
        lambda cwd: "buchbend/unknown",
    )

    refs = collect_projects_for_session(
        cwd=tmp_path, files_touched=[], wiki_root=wiki,
    )
    assert refs == []


def test_collect_projects_picks_up_repo_prefixes_from_files_touched(tmp_path, monkeypatch):
    """Cross-project session: cwd is in repo A, but files in repo B were
    edited too. Both should land in projects: when their notes exist."""
    wiki = tmp_path / "wiki"
    (wiki / "projects").mkdir(parents=True)
    (wiki / "projects" / "lore.md").write_text("---\ntype: project\n---\n")
    (wiki / "projects" / "data-transfer.md").write_text(
        "---\ntype: project\n---\n"
    )

    monkeypatch.setattr(
        "lore_curator.session_activity.current_repo",
        lambda cwd: "buchbend/lore",
    )

    refs = collect_projects_for_session(
        cwd=tmp_path,
        files_touched=[
            "/home/x/git/lore/lib/foo.py",
            "/home/x/git/data-transfer/src/bar.py",
        ],
        wiki_root=wiki,
    )
    # ``lore`` from cwd, ``data-transfer`` from the second files_touched path.
    assert "lore" in refs
    assert "data-transfer" in refs


# ---------------------------------------------------------------------------
# files_modified / files_read / files_touched split (step-1 of
# yes-do-that-keen-yeti). The narrative-shape gate that fixes the bad
# 05-1212 note depends on edits and reads being distinguishable at the
# helper layer.
# ---------------------------------------------------------------------------


def _turn_with_tool(idx: int, name: str, category: str, path: str):
    from lore_core.types import ToolCall, Turn
    return Turn(
        index=idx, timestamp=None, role="assistant",
        tool_call=ToolCall(name=name, input={"file_path": path}, id=f"tc{idx}", category=category),
    )


def test_files_modified_returns_edits_only():
    from lore_curator.session_activity import _files_modified_from_turns

    turns = [
        _turn_with_tool(0, "Read", "file_read", "a.py"),
        _turn_with_tool(1, "Edit", "file_edit", "b.py"),
        _turn_with_tool(2, "Read", "file_read", "c.py"),
        _turn_with_tool(3, "Write", "file_edit", "d.py"),
    ]
    assert _files_modified_from_turns(turns) == ["b.py", "d.py"]


def test_files_read_returns_reads_only():
    from lore_curator.session_activity import _files_read_from_turns

    turns = [
        _turn_with_tool(0, "Read", "file_read", "a.py"),
        _turn_with_tool(1, "Edit", "file_edit", "b.py"),
        _turn_with_tool(2, "Read", "file_read", "c.py"),
    ]
    assert _files_read_from_turns(turns) == ["a.py", "c.py"]


def test_files_touched_legacy_helper_returns_union():
    """The legacy union helper stays backward-compatible — buffer events
    archived under v1 still fold cleanly into ``rb.files_touched``."""
    from lore_curator.session_activity import _files_touched_from_turns

    turns = [
        _turn_with_tool(0, "Read", "file_read", "a.py"),
        _turn_with_tool(1, "Edit", "file_edit", "b.py"),
    ]
    assert _files_touched_from_turns(turns) == ["a.py", "b.py"]


def test_files_modified_dedupes_and_preserves_first_seen_order():
    from lore_curator.session_activity import _files_modified_from_turns

    turns = [
        _turn_with_tool(0, "Edit", "file_edit", "b.py"),
        _turn_with_tool(1, "Edit", "file_edit", "a.py"),
        _turn_with_tool(2, "Edit", "file_edit", "b.py"),
    ]
    assert _files_modified_from_turns(turns) == ["b.py", "a.py"]


def test_files_modified_ignores_non_file_categories():
    from lore_curator.session_activity import _files_modified_from_turns

    turns = [
        _turn_with_tool(0, "Bash", "shell_exec", "cmd"),
        _turn_with_tool(1, "Edit", "file_edit", "a.py"),
        _turn_with_tool(2, "Agent", "agent_spawn", "subagent"),
    ]
    assert _files_modified_from_turns(turns) == ["a.py"]


def test_collect_projects_dedupes(tmp_path, monkeypatch):
    wiki = tmp_path / "wiki"
    (wiki / "projects").mkdir(parents=True)
    (wiki / "projects" / "lore.md").write_text("---\ntype: project\n---\n")

    monkeypatch.setattr(
        "lore_curator.session_activity.current_repo",
        lambda cwd: "buchbend/lore",
    )

    refs = collect_projects_for_session(
        cwd=tmp_path,
        files_touched=[
            "/home/x/git/lore/a.py",
            "/home/x/git/lore/b.py",
        ],
        wiki_root=wiki,
    )
    assert refs == ["lore"]
