"""Spec branch of an epic: does `epic/<n>` need it merged in? (#556)

`to-epic` writes ``**Spec branch:** <name>`` and a ``**PRD:**`` link into the
epic body. A branch ref counts as holding a file when the local branch or the
``origin/`` copy does; a spec branch that exists only as ``origin/<name>``
therefore still counts.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

_BRANCH_RE = re.compile(r"\*\*Spec branch:\*\*\s*`?([^`\s]+)`?")
_PRD_RE = re.compile(r"\*\*PRD:\*\*[^\n]*?(docs/prd/[^)\s`\]]+\.md)")


class SpecBranchError(Exception):
    pass


@dataclass(frozen=True)
class SpecBranch:
    branch: str | None
    prd: str
    needs_merge: bool
    ref: str


def _holds(repo: Path, branch: str, path: str) -> str | None:
    """The ref that holds `path`: `origin/<branch>` first, else the local branch."""
    for ref in (f"origin/{branch}", branch):
        try:
            r = subprocess.run(
                ["git", "cat-file", "-e", f"{ref}:{path}"], cwd=str(repo), capture_output=True
            )
        except OSError as exc:
            raise SpecBranchError(f"cannot run git in {repo}: {exc}") from None
        if r.returncode == 0:
            return ref
    return None


def resolve_spec_branch(body: str, repo: Path, target_branch: str) -> SpecBranch:
    prd_m = _PRD_RE.search(body)
    if not prd_m:
        raise SpecBranchError("epic body has no **PRD:** link to a docs/prd/*.md file")
    prd = prd_m.group(1)
    branch_m = _BRANCH_RE.search(body)
    branch = branch_m.group(1) if branch_m else None
    if branch and branch.startswith("<"):  # template placeholder, not a branch
        branch = None
    if ref := _holds(repo, target_branch, prd):
        return SpecBranch(branch, prd, False, ref)
    if branch and (ref := _holds(repo, branch, prd)):
        return SpecBranch(branch, prd, True, ref)
    raise SpecBranchError(
        f"{prd} is not on {target_branch} and "
        + (f"spec branch {branch} lacks it" if branch else "the body names no **Spec branch:**")
    )
