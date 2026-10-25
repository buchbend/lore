"""The local checkouts of the repos of a system map, and their git state.

``order`` sorts the repos of the maps producers first: a producer comes
before each repo that consumes it. ``locate`` finds the checkout of each
repo below the folders of the person's scopes. ``state`` reads one
checkout's branch, local changes and the refs of a target branch.

Everything here reads the local repository only; no call fetches. The
``lore:checkout`` skill fetches first, then reads ``lore map status``,
which prints these facts. The systems page shows them too.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

from lore_core.system_map import SystemMap


@dataclass(frozen=True)
class Checkout:
    """Where the checkout of *repo* sits: its scope and its folder."""

    repo: str
    scope: str
    path: Path


@dataclass(frozen=True)
class CheckoutState:
    """The git state of one checkout. ``target_*`` describe the asked branch."""

    repo: str
    path: str
    scope: str
    branch: str | None
    default_branch: str | None
    dirty: int
    ahead: int | None
    behind: int | None
    target: str | None = None
    target_local: bool = False
    target_remote: str | None = None
    target_elsewhere: str | None = None
    untracked: int = 0
    conflicts: int = 0
    operation: str | None = None
    unpushed: int = 0

    def as_dict(self) -> dict[str, object]:
        return {**asdict(self), "action": self.action(), "local_work": self.local_work()}

    def local_work(self) -> list[str]:
        """What a switch could bury: one reason per kind of work not on a remote."""
        found = []
        if self.operation:
            found.append(f"a {self.operation} in progress")
        if self.conflicts:
            found.append(f"{self.conflicts} unmerged files")
        if self.dirty:
            found.append(f"{self.dirty} changed files")
        if self.untracked:
            found.append(f"{self.untracked} untracked files")
        if self.unpushed:
            found.append(f"{self.unpushed} commits on no remote")
        return found

    def action(self) -> str:
        """What ``lore:checkout`` does with this checkout for the target branch.

        ``stop`` with local work (see :meth:`local_work`), ``blocked`` when another worktree holds
        the branch, ``pull`` on the branch already, ``switch`` where the branch
        exists, ``default`` where it does not: the checkout goes to its
        default branch. ``none`` without a target.
        """
        if not self.target:
            return "none"
        if self.local_work():
            return "stop"
        if self.branch == self.target:
            return "pull"
        if self.target_elsewhere:
            return "blocked"
        if self.target_local or self.target_remote:
            return "switch"
        return "default"


def order(maps: Iterable[SystemMap], system: str | None = None) -> list[str]:
    """The repos of *maps* (or of the map named *system*), producers first.

    Ties sort by name. Repos in a cycle follow the rest, by name.
    """
    chosen = [m for m in maps if system is None or m.name == system]
    names: dict[str, str] = {}
    producers: dict[str, set[str]] = {}
    for m in chosen:
        for repo in m.repos:
            names.setdefault(repo.casefold(), repo)
        for edge in m.edges:
            consumer = names.setdefault(edge.consumer.casefold(), edge.consumer)
            producer = names.setdefault(edge.producer.casefold(), edge.producer)
            if consumer.casefold() != producer.casefold():
                producers.setdefault(consumer.casefold(), set()).add(producer.casefold())
    done: list[str] = []
    left = set(names)
    while left:
        ready = sorted(k for k in left if not (producers.get(k, set()) & left))
        if not ready:
            done.extend(sorted(left))
            break
        done.extend(ready)
        left -= set(ready)
    return [names[k] for k in done]


def locate(repos: Iterable[str]) -> dict[str, Checkout]:
    """The checkout of each of *repos* below a scope folder, keyed by the name in *repos*."""
    from lore_core.system_map_scan import configured_scopes, find_checkouts

    wanted = {r.casefold(): r for r in repos}
    found: dict[str, Checkout] = {}
    for scope, folder, _tidy in configured_scopes():
        if not folder.is_dir():
            continue
        for slug, path in find_checkouts(folder).items():
            repo = wanted.get(slug.casefold())
            if repo and repo not in found:
                found[repo] = Checkout(repo=repo, scope=scope, path=path)
    return found


def _git(path: Path, *args: str) -> str | None:
    """The stripped stdout of ``git args`` in *path*, or None when git fails."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(path),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _default_branch(path: Path) -> str | None:
    head = _git(path, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if head:
        return head.split("/", 1)[-1]
    for name in ("main", "master"):
        if _git(path, "show-ref", "--verify", "--quiet", f"refs/heads/{name}") is not None:
            return name
    return None


def _elsewhere(path: Path, branch: str) -> str | None:
    """The other worktree that has *branch* checked out, if one does."""
    listing = _git(path, "worktree", "list", "--porcelain") or ""
    here = path.resolve()
    tree: str | None = None
    for line in listing.splitlines():
        if line.startswith("worktree "):
            tree = line.removeprefix("worktree ")
        elif line == f"branch refs/heads/{branch}" and tree and Path(tree).resolve() != here:
            return tree
    return None


_OPERATIONS = (
    ("MERGE_HEAD", "merge"),
    ("rebase-merge", "rebase"),
    ("rebase-apply", "rebase"),
    ("CHERRY_PICK_HEAD", "cherry-pick"),
    ("REVERT_HEAD", "revert"),
    ("BISECT_LOG", "bisect"),
)
_CONFLICT_CODES = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}


def _operation(path: Path) -> str | None:
    """The merge, rebase, cherry-pick, revert or bisect in progress, if one is."""
    for name, label in _OPERATIONS:
        found = _git(path, "rev-parse", "--git-path", name)
        if found and (path / found).exists():
            return label
    return None


def state(checkout: Checkout, target: str | None = None) -> CheckoutState:
    """The git state of *checkout*; with *target*, where that branch exists."""
    path = checkout.path
    branch = _git(path, "rev-parse", "--abbrev-ref", "HEAD")
    status = _git(path, "status", "--porcelain=v1", "--untracked-files=normal") or ""
    codes = [line[:2] for line in status.splitlines()]
    # Commits HEAD holds that no remote-tracking ref holds: never pushed.
    unpushed = _git(path, "rev-list", "--count", "HEAD", "--not", "--remotes")
    counts = _git(path, "rev-list", "--left-right", "--count", "HEAD...@{upstream}")
    ahead = behind = None
    if counts and len(counts.split()) == 2:
        ahead, behind = (int(n) for n in counts.split())
    found = CheckoutState(
        repo=checkout.repo,
        path=str(path),
        scope=checkout.scope,
        branch=None if branch in (None, "HEAD") else branch,
        default_branch=_default_branch(path),
        dirty=sum(1 for c in codes if c != "??" and c not in _CONFLICT_CODES),
        ahead=ahead,
        behind=behind,
        untracked=codes.count("??"),
        conflicts=sum(1 for c in codes if c in _CONFLICT_CODES),
        operation=_operation(path),
        unpushed=int(unpushed) if unpushed and unpushed.isdigit() else 0,
    )
    if not target:
        return found
    local = _git(path, "show-ref", "--verify", "--quiet", f"refs/heads/{target}") is not None
    remotes = _git(path, "for-each-ref", "--format=%(refname:short)", f"refs/remotes/*/{target}")
    remote = sorted(remotes.splitlines(), key=lambda r: (not r.startswith("origin/"), r))
    return CheckoutState(
        **{
            **asdict(found),
            "target": target,
            "target_local": local,
            "target_remote": remote[0] if remote else None,
            "target_elsewhere": _elsewhere(path, target),
        }
    )
