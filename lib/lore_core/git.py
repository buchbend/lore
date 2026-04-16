"""Git helpers — repo resolution, remote parsing, co-commit history.

Used by hooks to scope context to the current repository, and by the
curator / session skills to auto-populate `repos:` frontmatter.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


def git_repo_root(cwd: Path | str | None = None) -> Path | None:
    """Return the git repo root containing `cwd`, or None if not in a repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    path = result.stdout.strip()
    return Path(path) if path else None


# Match: git@github.com:org/name.git, https://github.com/org/name,
# https://example.org/gitlab/group/name.git, etc.
_REMOTE_RE = re.compile(
    r"""
    (?:git@[^:]+:|https?://[^/]+/)   # auth prefix
    (?P<path>[^\s]+?)                 # org/name path
    (?:\.git)?$                       # optional .git suffix
    """,
    re.VERBOSE,
)


def canonical_repo_name(remote_url: str) -> str | None:
    """Normalize a remote URL to `org/name` form. Returns None if unparsable."""
    remote_url = remote_url.strip()
    if not remote_url:
        return None
    m = _REMOTE_RE.search(remote_url)
    if not m:
        return None
    path = m.group("path").strip("/")
    # Keep the last two path components: `group/subgroup/name` → `subgroup/name`
    parts = path.split("/")
    if len(parts) < 2:
        return None
    return "/".join(parts[-2:])


def current_repo(cwd: Path | str | None = None) -> str | None:
    """Resolve the current repo as `org/name`. Returns None if not in a repo."""
    root = git_repo_root(cwd)
    if root is None:
        return None
    for remote_name in ("upstream", "origin"):
        try:
            result = subprocess.run(
                ["git", "remote", "get-url", remote_name],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0 and result.stdout.strip():
            canonical = canonical_repo_name(result.stdout.strip())
            if canonical:
                return canonical
    return None


def repos_touched_in_range(
    repo_root: Path, since: str = "HEAD~20", until: str = "HEAD"
) -> list[str]:
    """List repos touched by commits in a git range. For session auto-tagging.

    Here `repo_root` is the wiki repo; we return all external repo refs
    mentioned in commit messages or metadata. Callers that want the
    repo this cwd lives in should use `current_repo()`.
    """
    try:
        result = subprocess.run(
            ["git", "log", "--format=%B", f"{since}..{until}"],
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
    # Extract `org/name` patterns from commit bodies (best-effort)
    found = set(re.findall(r"\b[\w.-]+/[\w.-]+\b", result.stdout))
    return sorted(found)


def is_obsidian_holding(path: Path) -> bool:
    """Return True if Obsidian appears to hold an edit lock on `path`'s vault.

    Cheap heuristic: check for Obsidian's lock file pattern. Curator
    invokes this before patching to avoid last-writer-wins races.
    """
    # Obsidian does not create a hard lock; we approximate by checking
    # for recent .obsidian/workspace state changes in the containing tree.
    # A more robust check would require OS-specific process inspection.
    for parent in [path, *path.parents]:
        if (parent / ".obsidian").is_dir():
            return True
    return False
