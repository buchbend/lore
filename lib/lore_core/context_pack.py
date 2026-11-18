"""Deterministic context pack — the join `lore_context_pack` exposes.

Bounded pointer pack: ADR/PRD entries and epic state for a given
cwd/branch/issue, joined purely on linkage keys (repo, scope, issue/epic
numbers) drawn from the branch name, commit text and `repo_docs` — never
an LLM call, never an FTS ranking dressed up as a join (PRD 0004). Cold
start (no git repo, no vault, no attached scope) degrades to a
well-formed empty pack, never an error envelope: this is a planning
front door and must always return something usable. Bodies are pulled
selectively afterward via `lore_read` / `lore_repo_docs_fetch` — never
eagerly inlined here.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from lore_core.gh import gh_issue_view
from lore_core.git import git_repo_root
from lore_core.linkage import Linkage, classify_refs, extract_linkage
from lore_core.repo_docs import is_shipped, list_docs, rank_docs
from lore_core.route import resolve_route


def _repo_root(cwd: Path, repo_path: str | None) -> Path | None:
    """Resolve the connected repo's root. Explicit `repo_path` wins (test seam)."""
    if repo_path:
        p = Path(repo_path).expanduser().resolve()
        return p if p.is_dir() else None
    return git_repo_root(cwd)


def _focus_issues(linkage: Linkage, branch: str | None, issue: int | None) -> set[int]:
    """Return every focus number — issues and epics alike drive the gh lookup."""
    issues = set(linkage.issues)
    epics = set(linkage.epics)
    if branch and branch != linkage.branch:
        i, _p, e = classify_refs(branch)
        issues |= i
        epics |= e
    if issue is not None:
        issues.add(issue)
    return issues | epics


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


#: Cap on the ranked docs an intent returns (PRD 0024).
INTENT_TOP = 8

_WORD = re.compile(r"[a-z0-9]{3,}")
_SUPERSEDED = re.compile(r"^\W*status\W*\s*(superseded|deprecated)", re.I | re.M)


# ponytail: a fixed English stop list; swap for a tf-idf weight if it ranks badly.
_STOP = frozenset(
    [
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "into",
        "onto",
        "over",
        "when",
        "then",
        "than",
        "what",
        "which",
        "who",
        "how",
        "why",
        "are",
        "was",
        "were",
        "has",
        "have",
        "had",
        "not",
        "but",
        "all",
        "any",
        "can",
        "its",
        "our",
        "out",
        "add",
        "fix",
        "make",
        "use",
        "new",
        "one",
        "two",
        "get",
        "set",
        "let",
        "should",
        "would",
        "could",
        "also",
    ]
)


def intent_words(intent: str) -> set[str]:
    """Lowercase words of three or more letters or digits, stop words dropped."""
    return set(_WORD.findall(intent.lower())) - _STOP


def _rank_by_intent(
    repo_root: Path, docs: list[dict[str, Any]], intent: str, limit: int = INTENT_TOP
) -> list[dict[str, Any]]:
    """Top `INTENT_TOP` docs that name an intent word, title hits weighted 3x.

    A superseded, deprecated or shipped doc ranks below every current doc;
    ties break on path, so the order is stable.
    """
    words = intent_words(intent)
    scored = []
    for doc in docs:
        if doc["is_index"]:  # an index names every title; it would always win
            continue
        title = _WORD.findall(str(doc["title"]).lower())
        body = _WORD.findall((repo_root / doc["path"]).read_text(errors="replace").lower())
        score = sum(3 * title.count(w) + body.count(w) for w in words)
        if score:
            old = is_shipped(doc) or not _is_current(repo_root, doc)
            scored.append((old, -score, doc["path"], doc))
    return [d for *_k, d in sorted(scored, key=lambda t: t[:3])][:limit]


def _is_current(repo_root: Path, doc: dict[str, Any]) -> bool:
    """False for an ADR whose status line says superseded or deprecated."""
    text = (repo_root / doc["path"]).read_text(errors="replace")
    header = re.split(r"^## ", text, maxsplit=1, flags=re.M)[0]  # the status line sits above it
    section = re.search(r"^## Status\s*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    status = f"status: {section.group(1).strip()}" if section else ""
    return not _SUPERSEDED.search(f"status: {doc.get('status') or ''}\n{header}\n{status}")


def _issue_state(repo: str, numbers: set[int]) -> list[dict[str, Any]]:
    """Fetch each focus issue's number, title, state and body.

    One failed fetch drops its own entry only — a rate-limited or deleted
    issue must not cost the pack the rest of the session's focus.
    """
    if not repo:
        return []
    out = []
    for num in sorted(numbers):
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
    intent: str | None = None,
) -> dict[str, Any]:
    """Join git-derived linkage + repo_docs into a bounded pointer pack.

    With an `intent`, the docs are ranked by a lexical match against title
    and body and capped at `INTENT_TOP`. With neither intent nor focus
    issue, the pack holds the current ADRs only: no PRD, no superseded ADR.
    """
    cwd_path = Path(cwd).expanduser().resolve() if cwd else Path.cwd()
    linkage = extract_linkage(cwd=cwd_path)
    repo_root = _repo_root(cwd_path, repo_path)
    focus = _focus_issues(linkage, branch, issue)

    route = resolve_route(cwd_path)
    scope = route.scope or ""
    wiki = route.wiki or ""

    if repo_root is not None and intent and intent_words(intent):
        # The intent ranks every doc: a focus branch must not hide a doc
        # that names the work but not the issue number.
        adrs, prds = list_docs(repo_root, "adr"), list_docs(repo_root, "prd")
        # Focus docs come first and count toward the cap.
        head = [
            d
            for d in _matching_docs(repo_root, "adr", focus)
            + _matching_docs(repo_root, "prd", focus)
            if focus and not d["is_index"]
        ]
        seen = {d["path"] for d in head}
        rest = [d for d in adrs + prds if d["path"] not in seen]
        top = (head + _rank_by_intent(repo_root, rest, intent, max(0, INTENT_TOP - len(head))))[
            :INTENT_TOP
        ]
        keep = {d["path"] for d in top}
        adrs = [d for d in adrs if d["path"] in keep]
        prds = [d for d in prds if d["path"] in keep]
        ranked = [d["path"] for d in top]
    else:
        adrs = _matching_docs(repo_root, "adr", focus)
        prds = _matching_docs(repo_root, "prd", focus)
        if repo_root is not None and not focus:
            adrs = [d for d in adrs if not d["is_index"] and _is_current(repo_root, d)]
            prds = []
        # Paths only: ADRs, then current PRDs, then shipped PRDs (ADR 0015).
        ranked = rank_docs(adrs, prds)
    return {
        "schema": "lore.context_pack/1",
        "repo": linkage.repo,
        "branch": branch or linkage.branch,
        "scope": scope,
        "wiki": wiki,
        "focus_issues": sorted(focus),
        "adr": adrs,
        "prd": prds,
        "intent": intent or "",
        "ranked": ranked,
        "epic_state": _issue_state(linkage.repo, focus),
    }
