"""Session note scaffolding — deterministic gather + frontmatter + path.

Used by:
  - lore_cli/session_cmd.py (`lore session new`, `lore session commit`)
  - lore_mcp/server.py     (MCP `lore_session_scaffold` tool, read-only)
  - the `lore-session-writer` subagent (calls MCP read first, then CLI
    writes)

This is the deterministic spine of /lore:session: routing, identity,
paths, frontmatter, recent-commits enumeration. The LLM-judgment work —
body prose, concept extraction — stays in the subagent. Per the
CLI-first / token-economy thesis the subagent's tool budget shrinks
from ~6–8 calls to ~3 (1 MCP scaffold-read + 1 Bash write + 1 Bash
commit).
"""

from __future__ import annotations

import re
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

from lore_core.config import get_wiki_root
from lore_core.git import current_repo, git_repo_root
from lore_core.identity import (
    resolve_handle,
    session_note_dir,
    team_mode_active,
)


def _walk_up_lore_config(cwd: Path) -> tuple[Path, dict] | None:
    """Registry-backed lookup for the attachment covering ``cwd``.

    Name retained for compatibility with callers that still expect a
    (path, block_dict) tuple. Returns:
      * ``(synthetic_claude_md_path, {"wiki": ..., "scope": ..., ...})``
        for attached cwds (the block dict merges the attachment + any
        ``.lore.yml`` at the repo root, so fields like ``backend``,
        ``issues``, ``prs`` surface to callers that need them), or
      * ``None`` for unattached cwds.
    """
    from lore_core.offer import parse_lore_yml, FILENAME as LORE_YML
    from lore_core.scope_resolver import resolve_scope

    scope = resolve_scope(cwd)
    if scope is None:
        return None
    block = {"wiki": scope.wiki, "scope": scope.scope, "backend": scope.backend}

    # Attachment paths store the repo root; look for a `.lore.yml` there
    # and merge its non-routing fields (backend, issues, prs, wiki_source)
    # into the block dict. This keeps downstream callers
    # (`_session_start_from_lore`, etc.) working unchanged.
    repo_root = scope.claude_md_path.parent
    offer = parse_lore_yml(repo_root / LORE_YML)
    if offer is not None:
        if offer.backend:
            block["backend"] = offer.backend
        if offer.issues:
            block["issues"] = offer.issues
        if offer.prs:
            block["prs"] = offer.prs
        if offer.wiki_source:
            block["wiki_source"] = offer.wiki_source

    return scope.claude_md_path, block


# ---------------------------------------------------------------------------
# Git-state helpers (lightweight, no caching)
# ---------------------------------------------------------------------------


def _git_user_email(cwd: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "config", "user.email"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _recent_commits(repo_root: Path, since: str = "24 hours ago") -> list[dict]:
    """Recent commits in `repo_root` since the given relative time."""
    try:
        result = subprocess.run(
            ["git", "log", f"--since={since}", "--format=%h%x09%s"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    commits: list[dict] = []
    for line in result.stdout.strip().splitlines():
        if "\t" in line:
            sha, _, msg = line.partition("\t")
            commits.append({"sha": sha, "message": msg})
    return commits


# ---------------------------------------------------------------------------
# Slug + frontmatter helpers
# ---------------------------------------------------------------------------


_SLUG_NONWORD = re.compile(r"[^\w\s-]")
_SLUG_DASH = re.compile(r"[\s_-]+")


def slugify(text: str) -> str:
    """Kebab-case slug from arbitrary text. Caps at 60 chars."""
    s = _SLUG_NONWORD.sub("", text.lower())
    s = _SLUG_DASH.sub("-", s).strip("-")
    return s[:60]


_FM_QUOTE_CHARS = ":#&*?|>!%@"


def _format_yaml_value(value: str) -> str:
    if any(c in value for c in _FM_QUOTE_CHARS):
        return '"' + value.replace('"', '\\"') + '"'
    return value


def format_frontmatter(fm: dict[str, Any]) -> str:
    """Serialize a session frontmatter dict to YAML between `---` markers.

    Conservative — we know the key set so we don't need PyYAML. Lists of
    strings render inline (`tags: [a, b, c]`); lists of multi-line items
    render as block.
    """
    lines = ["---"]
    for key, value in fm.items():
        if value is None or value == "" or value == []:
            continue
        if isinstance(value, list):
            if all(isinstance(v, str) and "\n" not in v for v in value):
                items = ", ".join(value)
                lines.append(f"{key}: [{items}]")
            else:
                lines.append(f"{key}:")
                for v in value:
                    lines.append(f"  - {v}")
        elif isinstance(value, dict):
            lines.append(f"{key}:")
            for k, v in value.items():
                lines.append(f"  {k}: {_format_yaml_value(str(v))}")
        elif isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        elif isinstance(value, int):
            lines.append(f"{key}: {value}")
        else:
            lines.append(f"{key}: {_format_yaml_value(str(value))}")
    lines.append("---")
    return "\n".join(lines)


BODY_TEMPLATE = """\
# Session: {title}

## What we worked on

- TODO

## Decisions made

- _None_

## Commits / PRs

{commits_section}

## Issues touched

- _None_

## Loose ends

- _None_

## Vault updates

- _None_
"""


# ---------------------------------------------------------------------------
# Scaffolder — single read-only entry point
# ---------------------------------------------------------------------------


def _resolve_wiki(wiki_root: Path, name: str) -> Path | None:
    candidate = wiki_root / name
    return candidate if candidate.exists() else None


def scaffold(
    *,
    cwd: str | Path,
    slug: str,
    description: str,
    title: str | None = None,
    target_wiki: str | None = None,
    extra_repos: list[str] | None = None,
    tags: list[str] | None = None,
    implements: list[str] | None = None,
    loose_ends: list[str] | None = None,
    project: str | None = None,
    when: date | None = None,
) -> dict[str, Any]:
    """Compute the path, frontmatter, and stub body for a new session note.

    Read-only — does NOT write the file. The CLI subcommand `lore
    session new` invokes scaffold() internally and writes the result;
    the MCP tool wraps scaffold() unchanged.

    Returned shape (always — caller checks `error` field if present):
        {
            "wiki": str | None,
            "wiki_path": str | None,
            "note_path": str | None,
            "relative_path": str | None,
            "frontmatter": dict | None,
            "frontmatter_yaml": str | None,
            "body_template": str | None,
            "handle": str | None,
            "scope": str | None,
            "team_mode": bool | None,
            "commit_log": list[dict],
            "existing": bool,
            "error": str | None,
        }
    """
    cwd_path = Path(cwd).resolve()
    when = when or date.today()
    today_iso = when.isoformat()

    wiki_root = get_wiki_root()
    if not wiki_root.exists():
        return {"error": f"No vault at {wiki_root}"}

    # 1. Routing — `## Lore` block is authoritative; otherwise fall back
    #    to the explicit target_wiki, otherwise to the only-wiki case.
    scope = ""
    config = _walk_up_lore_config(cwd_path)
    if config:
        _, block = config
        if not target_wiki and "wiki" in block:
            target_wiki = block["wiki"]
        scope = block.get("scope", "")

    wiki_path: Path | None = None
    if target_wiki:
        wiki_path = _resolve_wiki(wiki_root, target_wiki)
    if wiki_path is None:
        wikis = [p for p in sorted(wiki_root.iterdir()) if p.resolve().is_dir()]
        if len(wikis) == 1:
            wiki_path = wikis[0]
            target_wiki = wiki_path.name
    if wiki_path is None:
        return {
            "error": (
                "Could not resolve target wiki — pass target_wiki "
                "explicitly or attach this folder to a wiki via "
                "`lore attach`."
            )
        }

    if not scope:
        scope = wiki_path.name  # zero-config fallback

    # 2. Identity + path resolution (sharded in team mode, flat in solo).
    email = _git_user_email(cwd_path)
    handle = resolve_handle(wiki_path, email) if email else "unknown"
    team_mode = team_mode_active(wiki_path)
    sessions_dir = session_note_dir(wiki_path, handle)

    safe_slug = slugify(slug)
    if not safe_slug:
        return {"error": f"Slug `{slug}` slugified to empty — pick a different slug."}
    note_filename = f"{today_iso}-{safe_slug}.md"
    note_path = sessions_dir / note_filename
    relative_path = note_path.relative_to(wiki_path)
    existing = note_path.exists()

    # 3. Recent commits + repo for the work-side repo (NOT the wiki).
    repo = current_repo(cwd_path)
    repo_root = git_repo_root(cwd_path)
    commit_log = _recent_commits(repo_root) if repo_root else []

    repos_list: list[str] = [repo] if repo else []
    for r in extra_repos or []:
        if r not in repos_list:
            repos_list.append(r)

    # 4. Frontmatter — schema v2.
    fm: dict[str, Any] = {
        "schema_version": 2,
        "type": "session",
        "scope": scope,
        "user": handle,
        "created": today_iso,
        "last_reviewed": today_iso,
        "description": description,
    }
    if tags:
        fm["tags"] = tags
    if repos_list:
        fm["repos"] = repos_list
    if implements:
        fm["implements"] = implements
    if loose_ends:
        fm["loose_ends"] = loose_ends
    if project:
        fm["project"] = project

    fm_yaml = format_frontmatter(fm)

    # 5. Body template — frontmatter-aware so the subagent only needs to
    #    fill in prose. Pre-fills "Commits / PRs" from the git log.
    title_text = title or slug.replace("-", " ").strip().capitalize()
    if commit_log:
        commits_section = "\n".join(
            f"- `{c['sha']}` {c['message']}" + (f" ({repo})" if repo else "")
            for c in commit_log
        )
    else:
        commits_section = "- _None_"

    body = BODY_TEMPLATE.format(title=title_text, commits_section=commits_section)

    return {
        "wiki": wiki_path.name,
        "wiki_path": str(wiki_path),
        "note_path": str(note_path),
        "relative_path": str(relative_path),
        "frontmatter": fm,
        "frontmatter_yaml": fm_yaml,
        "body_template": body,
        "handle": handle,
        "scope": scope,
        "team_mode": team_mode,
        "commit_log": commit_log,
        "existing": existing,
    }


# ---------------------------------------------------------------------------
# Write + commit (side-effecting — exposed only via CLI, never MCP)
# ---------------------------------------------------------------------------


def write_note(
    *,
    note_path: Path,
    frontmatter_yaml: str,
    body: str,
) -> Path:
    """Materialise a session note. Creates parent dirs as needed.

    No safety net — caller must have run scaffold() first to know the
    intended path. Used by `lore session new`.
    """
    note_path.parent.mkdir(parents=True, exist_ok=True)
    text = frontmatter_yaml.rstrip() + "\n\n" + body.strip() + "\n"
    note_path.write_text(text)
    return note_path


def commit_note(
    *,
    wiki_path: Path,
    note_path: Path,
    message: str | None = None,
) -> tuple[bool, str]:
    """`git add` + `git commit` in the wiki repo.

    Returns (success, sha-or-error). Idempotent for already-committed
    state ("nothing to commit" returns True with empty sha).
    """
    rel = note_path.resolve().relative_to(wiki_path.resolve())
    add = subprocess.run(
        ["git", "add", str(rel)],
        cwd=str(wiki_path),
        capture_output=True,
        text=True,
        check=False,
    )
    if add.returncode != 0:
        return False, add.stderr.strip() or "git add failed"
    if message is None:
        # Default message from the slug
        slug = note_path.stem
        message = f"lore: session {slug}"
    commit = subprocess.run(
        ["git", "commit", "-m", message],
        cwd=str(wiki_path),
        capture_output=True,
        text=True,
        check=False,
    )
    if commit.returncode != 0:
        if "nothing to commit" in commit.stdout or "nothing to commit" in commit.stderr:
            return True, ""
        return False, commit.stderr.strip() or commit.stdout.strip()
    # Read back the new HEAD sha
    head = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=str(wiki_path),
        capture_output=True,
        text=True,
        check=False,
    )
    return True, head.stdout.strip() if head.returncode == 0 else ""
