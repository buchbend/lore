"""Tests for plans/breadcrumbs.py — informational scan over commits + sessions."""
from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lore_core.plans.breadcrumbs import (
    Breadcrumb,
    is_nudge,
    newest_per_step,
    scan_recent_commits,
    scan_recent_session_links,
)


# ---------------------------------------------------------------------------
# Commit-trailer scan
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_repo(tmp_path: Path) -> Path:
    """Initialize a tiny git repo with deterministic config — no global-config bleed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    for cmd in (
        ["git", "init", "--quiet", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
        ["git", "commit", "--allow-empty", "-m", "initial"],
    ):
        subprocess.run(cmd, cwd=repo, check=True, capture_output=True)
    return repo


def _commit(repo: Path, message: str) -> None:
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", message],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def test_scan_recent_commits_finds_trailer(fresh_repo: Path) -> None:
    _commit(fresh_repo, "Implement OIDC config\n\nPlan: refactor-auth#s1")
    crumbs = scan_recent_commits(fresh_repo, "refactor-auth")
    assert len(crumbs) == 1
    assert crumbs[0].step_id == "s1"
    assert crumbs[0].source == "commit"
    assert crumbs[0].extra == "Implement OIDC config"


def test_scan_recent_commits_multiple_steps_in_one_commit(fresh_repo: Path) -> None:
    _commit(
        fresh_repo,
        "Multi-step\n\nPlan: refactor-auth#s2\nPlan: refactor-auth#s3",
    )
    crumbs = scan_recent_commits(fresh_repo, "refactor-auth")
    step_ids = sorted(c.step_id for c in crumbs)
    assert step_ids == ["s2", "s3"]


def test_scan_recent_commits_filters_by_slug(fresh_repo: Path) -> None:
    _commit(fresh_repo, "wrong slug\n\nPlan: other-plan#s1")
    _commit(fresh_repo, "right slug\n\nPlan: refactor-auth#s2")
    crumbs = scan_recent_commits(fresh_repo, "refactor-auth")
    assert [c.step_id for c in crumbs] == ["s2"]


def test_scan_recent_commits_case_insensitive(fresh_repo: Path) -> None:
    _commit(fresh_repo, "case\n\nplan: refactor-auth#S3")
    crumbs = scan_recent_commits(fresh_repo, "Refactor-Auth")
    assert len(crumbs) == 1
    assert crumbs[0].step_id == "s3"


def test_scan_recent_commits_ignores_subject_only_mention(
    fresh_repo: Path,
) -> None:
    """Bare ``refactor-auth#s2`` in subject (no Plan: trailer) is NOT a breadcrumb.

    Trailer convention is the unambiguous signal; subject substrings
    risk false positives from prose like "fixes refactor-auth#s2 issue".
    """
    _commit(fresh_repo, "subject mentions refactor-auth#s2 but no trailer")
    crumbs = scan_recent_commits(fresh_repo, "refactor-auth")
    assert crumbs == []


def test_scan_recent_commits_returns_empty_on_missing_repo(tmp_path: Path) -> None:
    crumbs = scan_recent_commits(tmp_path / "nonexistent", "x")
    assert crumbs == []


def test_scan_recent_commits_returns_empty_on_non_git_dir(tmp_path: Path) -> None:
    """Existing dir but not a git repo — best-effort returns []."""
    (tmp_path / "notgit").mkdir()
    crumbs = scan_recent_commits(tmp_path / "notgit", "x")
    assert crumbs == []


def test_scan_recent_commits_newest_first(fresh_repo: Path) -> None:
    _commit(fresh_repo, "first\n\nPlan: x#s1")
    _commit(fresh_repo, "second\n\nPlan: x#s2")
    _commit(fresh_repo, "third\n\nPlan: x#s3")
    crumbs = scan_recent_commits(fresh_repo, "x")
    assert [c.step_id for c in crumbs] == ["s3", "s2", "s1"]


# ---------------------------------------------------------------------------
# Session-wikilink scan
# ---------------------------------------------------------------------------


def test_scan_session_links_finds_anchored_links(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    sessions = wiki / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "2026-04-28-x.md").write_text(
        "---\ntype: session\ncreated: 2026-04-28\n---\n\n"
        "Working on [[plan/refactor-auth#s2]]\n"
    )
    crumbs = scan_recent_session_links(wiki, "refactor-auth", days=30)
    assert len(crumbs) == 1
    assert crumbs[0].step_id == "s2"
    assert crumbs[0].source == "session"


def test_scan_session_links_ignores_bare_plan_mentions(tmp_path: Path) -> None:
    """[[plan/refactor-auth]] without ``#sN`` → not a step-level breadcrumb."""
    wiki = tmp_path / "wiki"
    sessions = wiki / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "2026-04-28-x.md").write_text(
        "---\ntype: session\ncreated: 2026-04-28\n---\n\n"
        "See [[plan/refactor-auth]] for context\n"
    )
    crumbs = scan_recent_session_links(wiki, "refactor-auth", days=30)
    assert crumbs == []


def test_scan_session_links_ignores_same_named_concept(tmp_path: Path) -> None:
    """``[[refactor-auth#s2]]`` (no ``plan/`` prefix) is NOT a plan breadcrumb.

    This is the discrimination test from the merciless review: a
    concept note with a same-named slug + ``s2`` heading would
    otherwise masquerade as a plan-step reference.
    """
    wiki = tmp_path / "wiki"
    sessions = wiki / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "2026-04-28-x.md").write_text(
        "---\ntype: session\ncreated: 2026-04-28\n---\n\n"
        "See [[refactor-auth#s2]] (different note)\n"
    )
    crumbs = scan_recent_session_links(wiki, "refactor-auth", days=30)
    assert crumbs == []


def test_scan_session_links_filters_by_age(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    sessions = wiki / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "old.md").write_text(
        "---\ntype: session\ncreated: 2025-01-01\n---\n\n[[plan/x#s1]]\n"
    )
    (sessions / "new.md").write_text(
        "---\ntype: session\ncreated: 2026-04-28\n---\n\n[[plan/x#s2]]\n"
    )
    crumbs = scan_recent_session_links(
        wiki, "x", days=14, now=datetime(2026, 4, 28, 12, 0, 0)
    )
    assert [c.step_id for c in crumbs] == ["s2"]


def test_scan_session_links_returns_empty_when_no_sessions_dir(
    tmp_path: Path,
) -> None:
    crumbs = scan_recent_session_links(tmp_path / "wiki", "x")
    assert crumbs == []


# ---------------------------------------------------------------------------
# is_nudge / newest_per_step
# ---------------------------------------------------------------------------


def test_is_nudge_true_when_step_pending_and_breadcrumb_newer() -> None:
    bc = Breadcrumb(
        step_id="s2",
        source="commit",
        ref="abc",
        ts=datetime(2026, 4, 28, 12, 0, 0, tzinfo=UTC),
    )
    assert is_nudge(
        bc,
        step_status={},
        step_status_updated=datetime(2026, 4, 28, 10, 0, 0, tzinfo=UTC),
    )


def test_is_nudge_false_when_step_already_done() -> None:
    bc = Breadcrumb(
        step_id="s2",
        source="commit",
        ref="abc",
        ts=datetime(2026, 4, 28, 12, 0, 0, tzinfo=UTC),
    )
    assert not is_nudge(
        bc,
        step_status={"s2": "done"},
        step_status_updated=datetime(2026, 4, 28, 10, 0, 0, tzinfo=UTC),
    )


def test_is_nudge_false_when_breadcrumb_older_than_status_update() -> None:
    bc = Breadcrumb(
        step_id="s2",
        source="commit",
        ref="abc",
        ts=datetime(2026, 4, 28, 9, 0, 0, tzinfo=UTC),
    )
    # Status was updated AFTER the commit — no nudge needed (user/Claude
    # saw the commit when they marked status).
    assert not is_nudge(
        bc,
        step_status={"s2": "in_progress"},
        step_status_updated=datetime(2026, 4, 28, 10, 0, 0, tzinfo=UTC),
    )


def test_is_nudge_true_when_no_status_update_yet() -> None:
    """Plan with no step_status touched at all → any breadcrumb nudges."""
    bc = Breadcrumb(
        step_id="s2",
        source="commit",
        ref="abc",
        ts=datetime(2026, 4, 28, 9, 0, 0, tzinfo=UTC),
    )
    assert is_nudge(bc, step_status={}, step_status_updated=None)


def test_newest_per_step_collapses() -> None:
    crumbs = [
        Breadcrumb("s1", "commit", "old", datetime(2026, 4, 27, 10, 0, 0)),
        Breadcrumb("s1", "commit", "new", datetime(2026, 4, 28, 10, 0, 0)),
        Breadcrumb("s2", "session", "z", datetime(2026, 4, 26, 0, 0, 0)),
    ]
    collapsed = newest_per_step(crumbs)
    assert collapsed["s1"].ref == "new"
    assert collapsed["s2"].ref == "z"
