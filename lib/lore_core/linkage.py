"""Deterministic linkage extraction — zero-LLM, zero-network cross-note refs.

Every session note gets a schema-versioned `linkage` snapshot: repo,
branch, and issue/PR/epic numbers found in the branch name and commit
text, plus the author's display name. Missing signals degrade to
absent/empty fields — this module never guesses.

Repo and branch come from `lore_core.git` (already-resolved git state).
Commit subject/body text is supplied by the caller (resolved via
`lore_curator.session_activity.collect_commits_by_sha`, a curator-layer
concern) so this module stays in the core layer with no upward import.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from lore_core.git import current_branch, current_repo
from lore_core.identity import resolve_display_name

__all__ = ["Linkage", "extract_linkage"]

SCHEMA_VERSION = 1

# Epic refs: "epic #162", "epic/162", "Epic 162".
_EPIC_RE = re.compile(r"\bepic[\s/#]*(\d+)\b", re.IGNORECASE)
# PR refs: "PR #200", "pr 200", or a GitHub PR URL's /pull/200.
_PR_RE = re.compile(r"\bpr[\s#]*(\d+)\b|/pull/(\d+)", re.IGNORECASE)
# Bare issue refs: "#175". Catch-all, so it runs last and excludes
# numbers already classified as epic or PR.
_ISSUE_HASH_RE = re.compile(r"#(\d+)\b")
# Feature-branch convention: "feat/175-...", "fix/42", "chore/9-cleanup".
_BRANCH_ISSUE_RE = re.compile(
    r"^(?:feat|fix|chore|feature|bug|bugfix)/(\d+)(?:[-/]|$)", re.IGNORECASE
)


@dataclass(frozen=True)
class Linkage:
    """Cross-note linkage frontmatter, schema-versioned for round-trip."""

    schema_version: int = SCHEMA_VERSION
    repo: str = ""
    branch: str = ""
    issues: list[int] = field(default_factory=list)
    prs: list[int] = field(default_factory=list)
    epics: list[int] = field(default_factory=list)
    author: str = ""


def _classify_refs(text: str) -> tuple[set[int], set[int], set[int]]:
    """Return (issues, prs, epics) found in `text`, mutually exclusive."""
    epics = {int(m) for m in _EPIC_RE.findall(text)}
    prs = {int(a or b) for a, b in _PR_RE.findall(text)} - epics
    issues = {int(m) for m in _ISSUE_HASH_RE.findall(text)} - epics - prs
    return issues, prs, epics


def extract_linkage(
    *,
    cwd: Path | str | None = None,
    commit_texts: list[str] | None = None,
    wiki_root: Path | None = None,
    handle: str = "",
) -> Linkage:
    """Derive linkage from git state, branch name, and commit text.

    `commit_texts` is one string per commit (subject + body); the caller
    resolves it (e.g. via `collect_commits_by_sha`) since fetching commit
    text is a curator-layer concern, not this module's.
    """
    branch = current_branch(cwd) or ""
    repo = current_repo(cwd) or ""

    issues: set[int] = set()
    prs: set[int] = set()
    epics: set[int] = set()
    for text in (branch, *(commit_texts or [])):
        i, p, e = _classify_refs(text)
        issues |= i
        prs |= p
        epics |= e
    m = _BRANCH_ISSUE_RE.match(branch)
    if m:
        issues.add(int(m.group(1)))
    issues -= prs | epics
    prs -= epics

    author = resolve_display_name(wiki_root, handle) if wiki_root and handle else ""

    return Linkage(
        repo=repo,
        branch=branch,
        issues=sorted(issues),
        prs=sorted(prs),
        epics=sorted(epics),
        author=author,
    )
