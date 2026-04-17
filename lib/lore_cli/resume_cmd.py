"""`lore resume --scope <prefix>` — aggregate open issues + PRs + recent
session notes across every repo in a scope subtree.

Used by the `/lore:resume` skill when the user passes a scope prefix
(e.g. `/lore:resume ccat:data-center`) to expand the SessionStart
"subtree collapsed" line.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from lore_core.config import get_wiki_root
from lore_core.gh import gh_issues, gh_prs
from lore_core.schema import parse_frontmatter
from lore_core.scopes import load_scopes_yml, subtree_members
from rich.console import Console

console = Console()

DEFAULT_ISSUES_FILTER = "--assignee @me --state open"
DEFAULT_PRS_FILTER = "--author @me"
RECENT_SESSION_DAYS = 30


def _resolve_wiki_for_scope(wiki_root: Path, scope_prefix: str) -> Path | None:
    """Find the wiki whose `_scopes.yml` claims the scope prefix.

    Walks each wiki's `_scopes.yml` and returns the first wiki where
    the prefix matches any leaf. Returns None if no wiki claims it —
    the caller should then prompt for `--wiki`.
    """
    for wiki in sorted(wiki_root.iterdir()):
        if not wiki.resolve().is_dir():
            continue
        scopes = load_scopes_yml(wiki)
        members = subtree_members(scopes, scope_prefix)
        if members:
            return wiki
    return None


def _recent_sessions_for_scope(
    wiki_path: Path,
    scope_prefix: str,
    member_repos: set[str],
    days: int = RECENT_SESSION_DAYS,
) -> list[tuple[str, str, str]]:
    """Return (date, slug, scope) tuples for recent session notes
    touching the subtree.

    A session note matches if its `scope:` frontmatter is the prefix
    or a descendant, OR its `repos:` list intersects `member_repos`.
    Walks both flat (`sessions/*.md`) and sharded (`sessions/*/*.md`).
    """
    sessions_dir = wiki_path / "sessions"
    if not sessions_dir.is_dir():
        return []
    cutoff = date.today() - timedelta(days=days)
    out: list[tuple[str, str, str]] = []
    for md in sorted(sessions_dir.rglob("*.md"), reverse=True):
        stem = md.stem
        try:
            d = date.fromisoformat(stem[:10])
        except (ValueError, IndexError):
            continue
        if d < cutoff:
            continue
        fm = parse_frontmatter(md.read_text(errors="replace"))
        note_scope = str(fm.get("scope") or "")
        note_repos = set(fm.get("repos") or [])
        matches_scope = bool(
            note_scope
            and (note_scope == scope_prefix or note_scope.startswith(scope_prefix + ":"))
        )
        matches_repos = bool(note_repos & member_repos)
        if matches_scope or matches_repos:
            out.append((stem[:10], stem[11:] or stem, note_scope or "—"))
    return out


def _build_markdown(
    scope_prefix: str,
    wiki_name: str,
    members: list[tuple[str, str]],
    issues_by_repo: dict[str, list[dict]],
    prs_by_repo: dict[str, list[dict]],
    sessions: list[tuple[str, str, str]],
) -> str:
    lines: list[str] = [f"## /lore:resume {scope_prefix}", ""]
    repo_list = ", ".join(sorted({repo for _, repo in members})) or "(none)"
    lines.append(f"Subtree in `{wiki_name}`: {len(members)} repo(s) — {repo_list}")
    lines.append("")

    # Issues
    any_issues = any(issues_by_repo.values())
    lines.append("### Open issues")
    if not any_issues:
        lines.append("_None matched._")
    else:
        for scope_path, repo in members:
            items = issues_by_repo.get(repo) or []
            if not items:
                continue
            lines.append(f"**{repo}** (`{scope_path}`)")
            for issue in items:
                n = issue.get("number")
                t = issue.get("title") or ""
                lines.append(f"- #{n} {t}".rstrip())
            lines.append("")
    lines.append("")

    # PRs
    any_prs = any(prs_by_repo.values())
    lines.append("### Open PRs")
    if not any_prs:
        lines.append("_None matched._")
    else:
        for scope_path, repo in members:
            items = prs_by_repo.get(repo) or []
            if not items:
                continue
            lines.append(f"**{repo}** (`{scope_path}`)")
            for pr in items:
                n = pr.get("number")
                t = pr.get("title") or ""
                draft = " [draft]" if pr.get("isDraft") else ""
                lines.append(f"- #{n}{draft} {t}".rstrip())
            lines.append("")
    lines.append("")

    # Sessions
    lines.append("### Recent session notes")
    if not sessions:
        lines.append("_None in the last 30 days._")
    else:
        for d, slug, note_scope in sessions[:20]:
            lines.append(f"- `{d}` {slug} — {note_scope}")
        if len(sessions) > 20:
            lines.append(f"- … +{len(sessions) - 20} more")
    lines.append("")

    return "\n".join(lines)


def run_resume(
    scope_prefix: str,
    wiki: str | None = None,
    issues_filter: str | None = None,
    prs_filter: str | None = None,
    json_output: bool = False,
) -> int:
    wiki_root = get_wiki_root()
    if not wiki_root.exists():
        console.print(f"[red]No vault at {wiki_root}[/red]")
        return 1

    wiki_path: Path | None = None
    if wiki:
        candidate = wiki_root / wiki
        if candidate.exists():
            wiki_path = candidate
    if wiki_path is None:
        wiki_path = _resolve_wiki_for_scope(wiki_root, scope_prefix)
    if wiki_path is None:
        console.print(
            f"[yellow]No wiki claims scope `{scope_prefix}`. "
            "Pass --wiki to select one.[/yellow]"
        )
        return 2

    scopes = load_scopes_yml(wiki_path)
    members = subtree_members(scopes, scope_prefix)
    if not members:
        console.print(
            f"[yellow]Scope `{scope_prefix}` has no members in "
            f"wiki/{wiki_path.name}/_scopes.yml.[/yellow]"
        )
        return 2

    issues_flags = issues_filter if issues_filter is not None else DEFAULT_ISSUES_FILTER
    prs_flags = prs_filter if prs_filter is not None else DEFAULT_PRS_FILTER

    issues_by_repo: dict[str, list[dict]] = {}
    prs_by_repo: dict[str, list[dict]] = {}
    for _scope_path, repo in members:
        if repo not in issues_by_repo:
            issues_by_repo[repo] = gh_issues(repo, issues_flags)
        if repo not in prs_by_repo:
            prs_by_repo[repo] = gh_prs(repo, prs_flags)

    member_repos = {repo for _, repo in members}
    sessions = _recent_sessions_for_scope(wiki_path, scope_prefix, member_repos)

    if json_output:
        print(
            json.dumps(
                {
                    "scope": scope_prefix,
                    "wiki": wiki_path.name,
                    "members": [{"scope": s, "repo": r} for s, r in members],
                    "issues": issues_by_repo,
                    "prs": prs_by_repo,
                    "sessions": [
                        {"date": d, "slug": s, "scope": sc} for d, s, sc in sessions
                    ],
                },
                indent=2,
            )
        )
    else:
        print(
            _build_markdown(
                scope_prefix,
                wiki_path.name,
                members,
                issues_by_repo,
                prs_by_repo,
                sessions,
            )
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lore-resume", description=__doc__)
    parser.add_argument("--scope", required=True, help="Scope prefix, e.g. ccat:data-center")
    parser.add_argument("--wiki", default=None, help="Wiki name (inferred from _scopes.yml if omitted)")
    parser.add_argument(
        "--issues",
        default=None,
        help=f"gh issue list filter flags (default: {DEFAULT_ISSUES_FILTER!r})",
    )
    parser.add_argument(
        "--prs",
        default=None,
        help=f"gh pr list filter flags (default: {DEFAULT_PRS_FILTER!r})",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of markdown")
    args = parser.parse_args(argv)

    return run_resume(
        scope_prefix=args.scope,
        wiki=args.wiki,
        issues_filter=args.issues,
        prs_filter=args.prs,
        json_output=args.json,
    )


if __name__ == "__main__":
    sys.exit(main())
