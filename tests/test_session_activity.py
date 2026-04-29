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
    collect_commits_in_window,
    collect_issues_in_window,
    collect_plans_advanced,
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
# collect_commits_in_window
# ---------------------------------------------------------------------------


def test_collect_commits_in_window_returns_commits_in_range(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo, remote_url="https://github.com/test/repo.git")
    t1 = datetime(2026, 4, 28, 10, 0, tzinfo=UTC)
    t2 = datetime(2026, 4, 28, 11, 0, tzinfo=UTC)
    t3 = datetime(2026, 4, 28, 12, 0, tzinfo=UTC)
    _commit(repo, subject="early commit", filename="a.txt", when=t1)
    _commit(repo, subject="middle commit closes #29", filename="b.txt", when=t2)
    _commit(repo, subject="late commit", filename="c.txt", when=t3)

    # Window covers only the middle commit.
    commits = collect_commits_in_window(
        repo,
        since=datetime(2026, 4, 28, 10, 30, tzinfo=UTC),
        until=datetime(2026, 4, 28, 11, 30, tzinfo=UTC),
    )
    assert len(commits) == 1
    assert commits[0].subject == "middle commit closes #29"
    assert commits[0].branch == "main"
    assert commits[0].repo == "test/repo"
    assert len(commits[0].short_hash) >= 4


def test_collect_commits_returns_empty_when_repo_missing(tmp_path):
    """Soft fail: missing repo path → []."""
    assert collect_commits_in_window(
        tmp_path / "does-not-exist",
        since=datetime(2026, 4, 28, tzinfo=UTC),
        until=datetime(2026, 4, 29, tzinfo=UTC),
    ) == []


def test_collect_commits_returns_empty_when_repo_root_is_none():
    assert collect_commits_in_window(
        None,
        since=datetime(2026, 4, 28, tzinfo=UTC),
        until=datetime(2026, 4, 29, tzinfo=UTC),
    ) == []


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
# collect_plans_advanced
# ---------------------------------------------------------------------------


def test_collect_plans_advanced_validates_against_wiki_plans_dir(tmp_path):
    """Only wikilinks pointing to real plan files in the wiki are kept."""
    wiki = tmp_path / "wiki"
    plans = wiki / "plans"
    plans.mkdir(parents=True)
    (plans / "real-plan.md").write_text("---\ntype: plan\n---\n")
    # ``ghost-plan`` does not exist on disk → should be dropped.

    body = (
        "Worked on [[plan/real-plan#s2]] today, also touched "
        "[[plan/ghost-plan#s1]] which is hallucinated. And "
        "[[plan/real-plan]] without a step too."
    )
    refs = collect_plans_advanced(
        repo_root=None, body_text=body, wiki_root=wiki,
    )
    assert "real-plan#s2" in refs
    assert "real-plan" in refs  # step-less form preserved
    assert all("ghost-plan" not in r for r in refs)


def test_collect_plans_advanced_dedupes(tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "plans").mkdir(parents=True)
    (wiki / "plans" / "p.md").write_text("---\ntype: plan\n---\n")

    body = "[[plan/p#s1]] and [[plan/p#s1]] again"
    refs = collect_plans_advanced(repo_root=None, body_text=body, wiki_root=wiki)
    assert refs == ["p#s1"]


def test_collect_plans_advanced_empty_when_no_plans_dir(tmp_path):
    """Wiki without a plans/ dir → no plans regardless of body."""
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    body = "[[plan/foo#s1]]"
    refs = collect_plans_advanced(repo_root=None, body_text=body, wiki_root=wiki)
    assert refs == []


def _commit_with_body(repo_root: Path, *, subject: str, body: str, filename: str, when: datetime) -> None:
    """Commit with a multi-line message (subject + trailer body)."""
    (repo_root / filename).write_text(filename)
    subprocess.run(["git", "add", "-A"], cwd=repo_root, check=True)
    iso = when.isoformat()
    env = {
        **__import__("os").environ,
        "GIT_AUTHOR_DATE": iso,
        "GIT_COMMITTER_DATE": iso,
    }
    msg = f"{subject}\n\n{body}\n"
    subprocess.run(
        ["git", "commit", "-q", "-F", "-", "--no-verify"],
        cwd=repo_root, check=True, input=msg, text=True, env=env,
    )


def test_collect_plans_advanced_trailer_only_within_window(tmp_path):
    """Trailers from commits OUTSIDE the chunk window must not bleed in.

    Regression: previously the trailer scan walked the last 200 commits
    unconditionally, so a long-lived ``Plan: <slug>#sN`` trailer tagged
    every later session even when the session had nothing to do with it.
    """
    wiki = tmp_path / "wiki"
    plans = wiki / "plans"
    plans.mkdir(parents=True)
    (plans / "old-plan.md").write_text("---\ntype: plan\n---\n")
    (plans / "new-plan.md").write_text("---\ntype: plan\n---\n")

    repo = tmp_path / "repo"
    _init_repo(repo)
    # Old commit BEFORE the window: trailer for old-plan.
    _commit_with_body(
        repo,
        subject="legacy work",
        body="Plan: old-plan#s1\nPlan: old-plan#s2",
        filename="legacy.txt",
        when=datetime(2026, 4, 28, 23, 48, tzinfo=UTC),
    )
    # In-window commit: trailer for new-plan.
    _commit_with_body(
        repo,
        subject="this session's work",
        body="Plan: new-plan#s1",
        filename="now.txt",
        when=datetime(2026, 4, 29, 6, 50, tzinfo=UTC),
    )

    refs = collect_plans_advanced(
        repo_root=repo,
        body_text="",
        wiki_root=wiki,
        since=datetime(2026, 4, 29, 6, 30, tzinfo=UTC),
        until=datetime(2026, 4, 29, 7, 0, tzinfo=UTC),
    )

    assert "new-plan#s1" in refs
    assert all(not r.startswith("old-plan") for r in refs), (
        f"old-plan trailer leaked across window boundary: {refs}"
    )


def test_collect_plans_advanced_trailer_drops_unknown_slugs(tmp_path):
    """Trailers that name a slug with no plans/<slug>.md are dropped."""
    wiki = tmp_path / "wiki"
    plans = wiki / "plans"
    plans.mkdir(parents=True)
    (plans / "real.md").write_text("---\ntype: plan\n---\n")

    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_with_body(
        repo,
        subject="work",
        body="Plan: real#s1\nPlan: ghost#s2",
        filename="x.txt",
        when=datetime(2026, 4, 29, 6, 50, tzinfo=UTC),
    )

    refs = collect_plans_advanced(
        repo_root=repo,
        body_text="",
        wiki_root=wiki,
        since=datetime(2026, 4, 29, 6, 30, tzinfo=UTC),
        until=datetime(2026, 4, 29, 7, 0, tzinfo=UTC),
    )

    assert "real#s1" in refs
    assert all("ghost" not in r for r in refs)


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
