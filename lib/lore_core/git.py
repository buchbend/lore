"""Git helpers — repo resolution and remote parsing.

Hooks, linkage and the system map use them to scope work to the current
repository.
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
# ssh://git@github.com:22/org/name.git, git+ssh://…, git://…,
# https://example.org/gitlab/group/name.git, etc.
_REMOTE_RE = re.compile(
    r"""
    (?:git@[^:/]+:|[\w+]+://(?:[^@/]+@)?[^/]+/)   # auth prefix
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


def current_branch(cwd: Path | str | None = None) -> str:
    """Resolve the current branch name for `cwd`. Empty string on any failure."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


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
