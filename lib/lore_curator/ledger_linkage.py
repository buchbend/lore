"""Build the transcript ledger's linkage block. Zero LLM, zero network.

The block answers "where did this session work, and what did it touch?" —
``repo``, ``branch``, ``prs``, ``issues``, ``commits``, ``files``. It is
the personal layer's only index: the owner drills their own archive with
it, and the SessionStart recap renders from it.

Two depths, because the two sources cost very different amounts:

* **shallow** (``turns=None``) — git state and the branch name only. Two
  ``git`` invocations; cheap enough for every capture hook. Returns
  ``repo``/``branch``/``prs``/``issues`` and nothing else, so a caller
  merging it over a stored block never clobbers a deep pass's results.
* **deep** (``turns`` supplied) — adds ``commits`` and ``files`` read out
  of the transcript itself, and widens the ref set with the commit
  subjects. Costs a full transcript parse, so callers run it at a session
  boundary or in a migration, not per prompt.

Refs are classified by syntax (``lore_core.linkage``), never by asking
GitHub: a bare ``#42`` stays an issue. Under-claiming beats a network
round-trip per ref.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from lore_core.linkage import extract_linkage

if TYPE_CHECKING:
    from lore_core.types import Turn

__all__ = ["build_linkage"]

#: Per-entry caps. The ledger sits on the capture hot path — it is read
#: five-plus times per hook — so one runaway session must not double the
#: file every reader parses. First-seen order is kept, so the caps drop
#: the tail of a long session, not its opening.
#: ponytail: fixed caps; make them config keys only if a real session
#: loses linkage that someone actually went looking for.
MAX_FILES = 50
MAX_COMMITS = 20


def build_linkage(
    cwd: Path | str | None,
    *,
    turns: list[Turn] | None = None,
    branch: str | None = None,
) -> dict[str, Any]:
    """Return the linkage block for a session working in ``cwd``.

    Keys present depend on the depth — see the module docstring. Callers
    merge the result over the entry's stored block rather than replacing
    it.

    ``branch`` overrides git's current branch, for a caller reading a
    session that ran on a branch the checkout has since left.
    """
    commit_texts: list[str] = []
    commits: list[str] = []
    files: list[str] = []

    if turns is not None:
        from lore_curator.session_activity import (
            _all_turn_text,
            _commit_shas_from_bash_results,
            _files_modified_from_turns,
        )

        commits = _commit_shas_from_bash_results(turns)[:MAX_COMMITS]
        files = [
            _repo_relative(p, cwd)
            for p in _files_modified_from_turns(turns)[:MAX_FILES]
        ]
        commit_texts = [_all_turn_text(turns)]

    linkage = extract_linkage(cwd=cwd, commit_texts=commit_texts, branch=branch)

    # An epic is an issue on GitHub; the ledger indexes both under
    # ``issues`` so "which sessions touched #362" answers for an epic too.
    issues = sorted(set(linkage.issues) | set(linkage.epics))

    block: dict[str, Any] = {
        "repo": linkage.repo,
        "branch": linkage.branch,
        "prs": list(linkage.prs),
        "issues": issues,
    }
    if turns is not None:
        block["commits"] = commits
        block["files"] = files
    return block


def _repo_relative(path: str, cwd: Path | str | None) -> str:
    """Strip the checkout prefix so the same file matches across worktrees.

    Falls back to the path as written when it lies outside the repo (or
    when there is no repo) — a wrong-looking absolute path is still a
    usable pointer; a silently dropped one is not.
    """
    from lore_core.git import git_repo_root

    root = git_repo_root(Path(cwd)) if cwd else None
    if root is None:
        return path
    try:
        return str(Path(path).relative_to(root))
    except ValueError:
        return path

