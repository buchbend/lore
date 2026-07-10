"""Tests for lore_core.linkage — deterministic cross-note ref extraction.

Zero-LLM, zero-network: repo/branch come from git state already resolved
by lore_core.git; issue/PR/epic refs come from regex classification of
the branch name plus commit subject/body text handed in by the caller.
Missing signals degrade to absent fields, never guesses.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml
from lore_core.linkage import Linkage, extract_linkage

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _init_repo(repo_root: Path, *, branch: str = "main", remote_url: str | None = None) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", branch], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo_root, check=True)
    if remote_url:
        subprocess.run(["git", "remote", "add", "origin", remote_url], cwd=repo_root, check=True)
    (repo_root / "f.txt").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=repo_root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init", "--no-verify"], cwd=repo_root, check=True)


# ---------------------------------------------------------------------------
# repo / branch resolution
# ---------------------------------------------------------------------------


def test_extract_linkage_resolves_repo_and_branch(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(
        repo, branch="feat/175-linkage-frontmatter", remote_url="git@github.com:buchbend/lore.git"
    )
    linkage = extract_linkage(cwd=repo)
    assert linkage.repo == "buchbend/lore"
    assert linkage.branch == "feat/175-linkage-frontmatter"


def test_extract_linkage_outside_repo_degrades_to_empty(tmp_path):
    linkage = extract_linkage(cwd=tmp_path)
    assert linkage == Linkage()


# ---------------------------------------------------------------------------
# issue / PR / epic classification
# ---------------------------------------------------------------------------


def test_extract_linkage_issue_number_from_feature_branch(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo, branch="feat/175-linkage-frontmatter")
    linkage = extract_linkage(cwd=repo)
    assert linkage.issues == [175]


def test_extract_linkage_epic_number_from_branch(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo, branch="epic/162")
    linkage = extract_linkage(cwd=repo)
    assert linkage.epics == [162]


def test_extract_linkage_classifies_commit_text_mutually_exclusive(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo, branch="main")
    linkage = extract_linkage(
        cwd=repo,
        commit_texts=["Closes #175, part of epic #162, see PR #200"],
    )
    assert linkage.issues == [175]
    assert linkage.prs == [200]
    assert linkage.epics == [162]


def test_extract_linkage_dedupes_across_branch_and_commits(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo, branch="feat/175-linkage-frontmatter")
    linkage = extract_linkage(cwd=repo, commit_texts=["Closes #175"])
    assert linkage.issues == [175]


# ---------------------------------------------------------------------------
# author (vault config, never $USER)
# ---------------------------------------------------------------------------


def test_extract_linkage_author_from_users_yml(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()
    (wiki_root / "_users.yml").write_text(
        yaml.safe_dump(
            {
                "users": [{"handle": "alice", "display_name": "Alice A."}],
            }
        )
    )
    linkage = extract_linkage(cwd=repo, wiki_root=wiki_root, handle="alice")
    assert linkage.author == "Alice A."


def test_extract_linkage_no_handle_or_wiki_root_gives_empty_author(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    linkage = extract_linkage(cwd=repo)
    assert linkage.author == ""


# ---------------------------------------------------------------------------
# schema version
# ---------------------------------------------------------------------------


def test_linkage_default_schema_version_is_1():
    assert Linkage().schema_version == 1
