"""Deterministic context pack — the join `lore_context_pack` exposes.

Bounded pointer pack: session notes, ADR/PRD entries, and epic state
for a given cwd/branch/issue, joined purely on linkage keys (repo,
scope, issue/epic numbers) drawn from session-note frontmatter and
`repo_docs` — never an LLM call, never an FTS ranking dressed up as a
join (PRD 0004). Cold start (no git repo, no vault, no attached scope)
degrades to a well-formed empty pack, never an error envelope: this is
a planning front door and must always return something usable. Bodies
are pulled selectively afterward via `lore_read` / `lore_repo_docs_fetch`
— never eagerly inlined here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lore_core.config import get_wiki_root
from lore_core.gh import gh_issue_view
from lore_core.git import git_repo_root
from lore_core.linkage import Linkage, classify_refs, extract_linkage
from lore_core.repo_docs import list_docs
from lore_core.resume import _iter_session_notes, _list_wikis
from lore_core.schema import parse_frontmatter
from lore_core.scope_resolver import resolve_scope

MAX_SESSIONS = 10
# ponytail: scan window before the top-MAX_SESSIONS cut. A note past this
# age has no realistic shot at surviving the sort. Widen if a use case
# needs older joins.
SCAN_DAYS = 90


def _repo_root(cwd: Path, repo_path: str | None) -> Path | None:
    """Resolve the connected repo's root. Explicit `repo_path` wins (test seam)."""
    if repo_path:
        p = Path(repo_path).expanduser().resolve()
        return p if p.is_dir() else None
    return git_repo_root(cwd)


def _focus_issues(
    linkage: Linkage, branch: str | None, issue: int | None
) -> tuple[set[int], set[int]]:
    """Return (all focus numbers, epics-only) — epics drive the gh epic-state lookup."""
    issues = set(linkage.issues)
    epics = set(linkage.epics)
    if branch and branch != linkage.branch:
        i, _p, e = classify_refs(branch)
        issues |= i
        epics |= e
    if issue is not None:
        issues.add(issue)
    return issues | epics, epics


def _session_matches(fm: dict[str, Any], *, repo: str, scope: str, focus: set[int]) -> bool:
    linkage = fm.get("linkage") or {}
    if repo and linkage.get("repo") == repo:
        return True
    if scope and str(fm.get("scope") or "") == scope:
        return True
    refs = set(linkage.get("issues") or []) | set(linkage.get("epics") or [])
    return bool(focus) and bool(refs & focus)


def _matching_sessions(
    wiki_root: Path, *, repo: str, scope: str, focus: set[int]
) -> list[dict[str, Any]]:
    if not wiki_root.exists():
        return []
    out: list[dict[str, Any]] = []
    for wiki_path in _list_wikis(wiki_root):
        for d, md in _iter_session_notes(wiki_path, SCAN_DAYS):
            fm = parse_frontmatter(md.read_text(errors="replace"))
            if not _session_matches(fm, repo=repo, scope=scope, focus=focus):
                continue
            out.append(
                {
                    "wiki": wiki_path.name,
                    "path": str(md.relative_to(wiki_path)),
                    "date": d.isoformat(),
                    "description": fm.get("description"),
                }
            )
    out.sort(key=lambda s: s["date"], reverse=True)
    return out[:MAX_SESSIONS]


def _matching_docs(repo_root: Path | None, kind: str, focus: set[int]) -> list[dict[str, Any]]:
    if repo_root is None:
        return []
    entries = list_docs(repo_root, kind)
    if not focus:
        return entries
    out = []
    for entry in entries:
        text = (repo_root / entry["path"]).read_text(errors="replace")
        i, _p, e = classify_refs(text)
        if (i | e) & focus:
            out.append(entry)
    return out


def _epic_state(repo: str, epics: set[int]) -> list[dict[str, Any]]:
    if not repo:
        return []
    out = []
    for num in sorted(epics):
        info = gh_issue_view(repo, num)
        if info is not None:
            out.append(info)
    return out


def gather(
    *,
    cwd: Path | str | None = None,
    branch: str | None = None,
    issue: int | None = None,
    repo_path: str | None = None,
) -> dict[str, Any]:
    """Join session-note linkage + repo_docs into a bounded pointer pack."""
    cwd_path = Path(cwd).expanduser().resolve() if cwd else Path.cwd()
    linkage = extract_linkage(cwd=cwd_path)
    repo_root = _repo_root(cwd_path, repo_path)
    focus, epics = _focus_issues(linkage, branch, issue)

    scope_obj = resolve_scope(cwd_path)
    scope = scope_obj.scope if scope_obj else ""
    wiki = scope_obj.wiki if scope_obj else ""

    sessions = _matching_sessions(get_wiki_root(), repo=linkage.repo, scope=scope, focus=focus)

    return {
        "schema": "lore.context_pack/1",
        "repo": linkage.repo,
        "branch": branch or linkage.branch,
        "scope": scope,
        "wiki": wiki,
        "focus_issues": sorted(focus),
        "sessions": sessions,
        "adr": _matching_docs(repo_root, "adr", focus),
        "prd": _matching_docs(repo_root, "prd", focus),
        "epic_state": _epic_state(linkage.repo, epics),
    }
