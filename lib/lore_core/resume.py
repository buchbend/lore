"""Unified resume gathering for the vault.

Single entry point — `gather()` — covers no-arg, wiki-scoped, keyword,
and scope-prefix modes. Used by:
  - lore_cli/resume_cmd.py (CLI front-end, stdout markdown or --json)
  - lore_mcp/server.py     (MCP `lore_resume` tool)

This is the deterministic gather layer per the CLI-first / token-economy
thesis: skills and CLI both consume `gather()` output and never re-do
the work themselves.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from lore_core.config import get_wiki_root
from lore_core.gh import gh_issues, gh_prs
from lore_core.schema import parse_frontmatter
from lore_core.scopes import load_scopes_yml, subtree_members

DEFAULT_DAYS = 3
DEFAULT_KEYWORD_K = 5
DEFAULT_SCOPE_DAYS = 30
DEFAULT_ISSUES_FILTER = "--assignee @me --state open"
DEFAULT_PRS_FILTER = "--author @me"

# Lines marked with these markers are noise — dropped from open-items.
EPHEMERAL_MARKERS = ("(ephemeral)", "(trivial)", "(todo)", "(skip)")

# v1 schema "## Open items" section. v2 schema replaces this with
# "## Loose ends" + "## Issues touched" — extending the scrape to v2 is
# tracked separately; today the legacy form still covers most notes.
_OPEN_ITEMS_RE = re.compile(r"##\s+Open items\s*\n(.+?)(?=\n##|\Z)", re.DOTALL)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _list_wikis(wiki_root: Path) -> list[Path]:
    return [p for p in sorted(wiki_root.iterdir()) if p.resolve().is_dir()]


def _resolve_wiki(wiki_root: Path, name: str) -> Path | None:
    candidate = wiki_root / name
    return candidate if candidate.exists() else None


def _session_date_from_path(md: Path, sessions_dir: Path) -> date | None:
    """Extract the session date from either layout.

    Canonical sharded form (what `lore session new` writes):
        sessions[/handle]/YYYY/MM/DD-slug.md  → date from path parts.
    Legacy flat form (older notes, test fixtures):
        sessions/YYYY-MM-DD-slug.md           → date from stem[:10].

    Returns None when neither parse succeeds (file is not a session note).
    """
    try:
        rel_parts = md.relative_to(sessions_dir).parts
    except ValueError:
        return None
    # Sharded layout: trailing parts are .../YYYY/MM/DD-slug.md
    if len(rel_parts) >= 3:
        year_s, month_s = rel_parts[-3], rel_parts[-2]
        day_s = md.stem[:2]
        if year_s.isdigit() and month_s.isdigit() and day_s.isdigit():
            try:
                return date(int(year_s), int(month_s), int(day_s))
            except ValueError:
                pass
    # Flat layout fallback
    try:
        return date.fromisoformat(md.stem[:10])
    except (ValueError, IndexError):
        return None


def _iter_session_notes(wiki_path: Path, days: int) -> list[tuple[date, Path]]:
    """Return (date, path) for session notes newer than cutoff (sharded-aware)."""
    sessions_dir = wiki_path / "sessions"
    if not sessions_dir.is_dir():
        return []
    cutoff = date.today() - timedelta(days=days)
    out: list[tuple[date, Path]] = []
    for md in sessions_dir.rglob("*.md"):
        d = _session_date_from_path(md, sessions_dir)
        if d is None or d < cutoff:
            continue
        out.append((d, md))
    out.sort(reverse=True, key=lambda t: t[0])
    return out


def extract_open_items(text: str) -> list[str]:
    """Parse `## Open items`, filter out ephemeral/marker lines."""
    m = _OPEN_ITEMS_RE.search(text)
    if not m:
        return []
    items: list[str] = []
    for raw in m.group(1).splitlines():
        line = raw.strip()
        if not line.startswith("-"):
            continue
        body = line.lstrip("-").strip()
        if not body or body.lower() == "none":
            continue
        if any(marker in body for marker in EPHEMERAL_MARKERS):
            continue
        items.append(body)
    return items


# ---------------------------------------------------------------------------
# Gatherers
# ---------------------------------------------------------------------------


def _gather_recent(
    wiki_root: Path,
    *,
    wiki: str | None = None,
    days: int = DEFAULT_DAYS,
) -> dict[str, Any]:
    """Recent sessions across one wiki or all wikis."""
    if wiki:
        target = _resolve_wiki(wiki_root, wiki)
        wikis = [target] if target else []
    else:
        wikis = _list_wikis(wiki_root)

    sessions: list[dict] = []
    open_items: list[dict] = []
    seen_items: set[str] = set()

    for wiki_path in wikis:
        for d, md in _iter_session_notes(wiki_path, days):
            text = md.read_text(errors="replace")
            fm = parse_frontmatter(text)
            sessions.append(
                {
                    "wiki": wiki_path.name,
                    "path": str(md.relative_to(wiki_path)),
                    "date": d.isoformat(),
                    "title": md.stem,
                    "description": fm.get("description"),
                }
            )
            for body in extract_open_items(text):
                if body in seen_items:
                    continue
                seen_items.add(body)
                open_items.append(
                    {
                        "wiki": wiki_path.name,
                        "session": md.stem,
                        "text": body,
                    }
                )

    return {
        "mode": "recent",
        "wiki": wiki,
        "days": days,
        "sessions": sessions[:20],
        "open_items": open_items[:30],
    }


def _gather_keyword(
    wiki_root: Path,
    keyword: str,
    *,
    wiki: str | None = None,
    k: int = DEFAULT_KEYWORD_K,
) -> dict[str, Any]:
    """Ranked keyword search via FTS5 across the vault."""
    # Lazy import — keeps lore_core lean for callers that don't need search.
    from lore_search.fts import FtsBackend

    backend = FtsBackend()
    backend.reindex(wiki=wiki)
    hits = backend.search(keyword, wiki=wiki, k=k)
    return {
        "mode": "keyword",
        "keyword": keyword,
        "wiki": wiki,
        "notes": [
            {
                "wiki": h.wiki,
                "path": h.path,
                "filename": h.filename,
                "score": round(h.score, 3),
                "description": h.description,
                "tags": h.tags or [],
            }
            for h in hits
        ],
    }


def _gather_scope(
    wiki_root: Path,
    scope_prefix: str,
    *,
    wiki: str | None = None,
    issues_filter: str = DEFAULT_ISSUES_FILTER,
    prs_filter: str = DEFAULT_PRS_FILTER,
    days: int = DEFAULT_SCOPE_DAYS,
) -> dict[str, Any]:
    """Aggregate gh issues + PRs + recent session notes across a scope subtree."""
    wiki_path: Path | None = None
    if wiki:
        wiki_path = _resolve_wiki(wiki_root, wiki)
    if wiki_path is None:
        for w in _list_wikis(wiki_root):
            if subtree_members(load_scopes_yml(w), scope_prefix):
                wiki_path = w
                break
    if wiki_path is None:
        return {
            "mode": "scope",
            "scope": scope_prefix,
            "error": (
                f"No wiki claims scope `{scope_prefix}`. "
                "Pass --wiki to select one."
            ),
        }

    members = subtree_members(load_scopes_yml(wiki_path), scope_prefix)
    if not members:
        return {
            "mode": "scope",
            "scope": scope_prefix,
            "wiki": wiki_path.name,
            "error": (
                f"Scope `{scope_prefix}` has no members in "
                f"{wiki_path.name}/_scopes.yml."
            ),
        }

    issues_by_repo: dict[str, list[dict]] = {}
    prs_by_repo: dict[str, list[dict]] = {}
    for _scope_path, repo in members:
        if repo not in issues_by_repo:
            issues_by_repo[repo] = gh_issues(repo, issues_filter)
        if repo not in prs_by_repo:
            prs_by_repo[repo] = gh_prs(repo, prs_filter)

    member_repos = {repo for _, repo in members}
    sessions_dir = wiki_path / "sessions"
    sessions: list[dict] = []
    if sessions_dir.is_dir():
        cutoff = date.today() - timedelta(days=days)
        for md in sorted(sessions_dir.rglob("*.md"), reverse=True):
            d = _session_date_from_path(md, sessions_dir)
            if d is None or d < cutoff:
                continue
            fm = parse_frontmatter(md.read_text(errors="replace"))
            note_scope = str(fm.get("scope") or "")
            note_repos = set(fm.get("repos") or [])
            matches_scope = bool(
                note_scope
                and (
                    note_scope == scope_prefix
                    or note_scope.startswith(scope_prefix + ":")
                )
            )
            if matches_scope or (note_repos & member_repos):
                # In flat layout the slug starts at byte 11 ("YYYY-MM-DD-").
                # In sharded layout the filename is "DD-slug.md" — the slug
                # starts at byte 3.
                slug_start = 11 if md.parent == sessions_dir else 3
                sessions.append(
                    {
                        "date": d.isoformat(),
                        "slug": md.stem[slug_start:] or md.stem,
                        "scope": note_scope or "—",
                        "path": str(md.relative_to(wiki_path)),
                    }
                )

    return {
        "mode": "scope",
        "scope": scope_prefix,
        "wiki": wiki_path.name,
        "members": [{"scope": s, "repo": r} for s, r in members],
        "issues": issues_by_repo,
        "prs": prs_by_repo,
        "sessions": sessions,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def gather(
    *,
    scope: str | None = None,
    wiki: str | None = None,
    keyword: str | None = None,
    days: int = DEFAULT_DAYS,
    k: int = DEFAULT_KEYWORD_K,
    issues_filter: str = DEFAULT_ISSUES_FILTER,
    prs_filter: str = DEFAULT_PRS_FILTER,
) -> dict[str, Any]:
    """Unified resume gather — dispatches on the most specific mode.

    Priority: scope > keyword > recent (wiki-scoped or all wikis).

    Returns a structured dict; CLI front-end formats to markdown,
    MCP front-end returns JSON, skills display verbatim.
    """
    wiki_root = get_wiki_root()
    if not wiki_root.exists():
        return {"error": f"No vault at {wiki_root}"}
    if scope:
        return _gather_scope(
            wiki_root,
            scope,
            wiki=wiki,
            issues_filter=issues_filter,
            prs_filter=prs_filter,
        )
    if keyword:
        return _gather_keyword(wiki_root, keyword, wiki=wiki, k=k)
    return _gather_recent(wiki_root, wiki=wiki, days=days)


# ---------------------------------------------------------------------------
# Markdown formatter (shared by CLI and any caller wanting prose output)
# ---------------------------------------------------------------------------


def format_markdown(result: dict[str, Any]) -> str:
    """Render `gather()` output as markdown for human consumption."""
    if "error" in result and not result.get("mode"):
        return f"_{result['error']}_"
    mode = result.get("mode")
    if mode == "scope":
        return _format_scope(result)
    if mode == "keyword":
        return _format_keyword(result)
    return _format_recent(result)


def _format_recent(result: dict[str, Any]) -> str:
    days = result.get("days", DEFAULT_DAYS)
    wiki = result.get("wiki")
    scope_label = wiki or "all wikis"
    lines: list[str] = [f"## Resume: {scope_label} (last {days}d)", ""]

    sessions = result.get("sessions") or []
    if sessions:
        lines.append("### Recent sessions")
        for s in sessions:
            desc = f" — {s['description']}" if s.get("description") else ""
            lines.append(f"- `{s['date']}` [{s['wiki']}] {s['title']}{desc}")
        lines.append("")
    else:
        lines.append("_No recent sessions._")
        lines.append("")

    open_items = result.get("open_items") or []
    if open_items:
        lines.append("### Open items")
        for item in open_items:
            lines.append(f"- [{item['wiki']}] {item['text']}")
        lines.append("")
    return "\n".join(lines)


def _format_keyword(result: dict[str, Any]) -> str:
    keyword = result.get("keyword", "")
    wiki = result.get("wiki")
    scope_label = f" in {wiki}" if wiki else ""
    lines: list[str] = [f"## Resume: `{keyword}`{scope_label}", ""]
    notes = result.get("notes") or []
    if not notes:
        lines.append("_No matches._")
        return "\n".join(lines)
    lines.append("### Top matches")
    for n in notes:
        desc = f" — {n['description']}" if n.get("description") else ""
        lines.append(f"- `{n['wiki']}/{n['path']}` (score {n['score']}){desc}")
    return "\n".join(lines)


def _format_scope(result: dict[str, Any]) -> str:
    if "error" in result:
        return f"_{result['error']}_"
    scope = result["scope"]
    wiki = result["wiki"]
    members = result.get("members") or []
    lines: list[str] = [f"## /lore:resume {scope}", ""]
    repo_list = ", ".join(sorted({m["repo"] for m in members})) or "(none)"
    lines.append(f"Subtree in `{wiki}`: {len(members)} repo(s) — {repo_list}")
    lines.append("")

    issues = result.get("issues") or {}
    lines.append("### Open issues")
    if not any(issues.values()):
        lines.append("_None matched._")
    else:
        for m in members:
            items = issues.get(m["repo"]) or []
            if not items:
                continue
            lines.append(f"**{m['repo']}** (`{m['scope']}`)")
            for issue in items:
                n = issue.get("number")
                t = issue.get("title") or ""
                lines.append(f"- #{n} {t}".rstrip())
            lines.append("")
    lines.append("")

    prs = result.get("prs") or {}
    lines.append("### Open PRs")
    if not any(prs.values()):
        lines.append("_None matched._")
    else:
        for m in members:
            items = prs.get(m["repo"]) or []
            if not items:
                continue
            lines.append(f"**{m['repo']}** (`{m['scope']}`)")
            for pr in items:
                n = pr.get("number")
                t = pr.get("title") or ""
                draft = " [draft]" if pr.get("isDraft") else ""
                lines.append(f"- #{n}{draft} {t}".rstrip())
            lines.append("")
    lines.append("")

    sessions = result.get("sessions") or []
    lines.append("### Recent session notes")
    if not sessions:
        lines.append("_None in the last 30 days._")
    else:
        for s in sessions[:20]:
            lines.append(f"- `{s['date']}` {s['slug']} — {s['scope']}")
        if len(sessions) > 20:
            lines.append(f"- … +{len(sessions) - 20} more")
    return "\n".join(lines)
