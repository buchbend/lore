"""The local checkouts of the repos of a system map, and their git state.

The map names repos as ``owner/repo``, never a folder: folders differ between
people. ``locate`` finds each repo's checkout on this machine. A
``checkouts`` entry in the person's config pins one repo to a folder; the
rest are found below the scope folders, named by their git remote.

``order`` sorts the repos of the maps producers first: a producer comes
before each repo that consumes it. ``state`` reads one checkout's branch,
local work and the refs of a target branch. ``report`` gathers one system's
status for ``lore system status``. The target, the branch the person wants a
system on, is saved per system under ``<lore_root>/.lore/system-target/``.

Only ``report(fetch=True)`` reaches the network; everything else reads the
local repositories.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from lore_core.system_map import SystemMap

CONFIG_KEY = "checkouts"
TARGET_DIR = "system-target"


@dataclass(frozen=True)
class Checkout:
    """Where the checkout of *repo* sits, and how Lore found it.

    ``source`` is ``pinned`` for a ``checkouts`` entry of the config, else
    ``scope <name>``. ``scope`` names the scope whose folder holds it, if one does.
    """

    repo: str
    scope: str
    path: Path
    source: str = ""


@dataclass(frozen=True)
class CheckoutState:
    """The git state of one checkout. ``target_*`` describe the asked branch."""

    repo: str
    path: str
    scope: str
    source: str
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
        return {
            **asdict(self),
            "action": self.action(),
            "local_work": self.local_work(),
            "at_target": self.at_target(),
        }

    def at_target(self) -> bool | None:
        """Whether the checkout is where the target puts it; None without a target.

        A repo without the target branch belongs on its default branch.
        """
        if not self.target:
            return None
        if self.target_local or self.target_remote:
            return self.branch == self.target
        return self.branch is not None and self.branch == self.default_branch

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
        """What ``lore:system`` does with this checkout for the target branch.

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


def pinned_checkouts() -> dict[str, Path]:
    """The ``checkouts`` entries of the person's config: ``owner/repo`` → folder."""
    from lore_core.config import user_config_path
    from lore_core.config_schema import _read_raw

    raw = _read_raw(user_config_path()).get(CONFIG_KEY)
    if not isinstance(raw, dict):
        return {}
    return {
        str(repo): Path(folder).expanduser()
        for repo, folder in raw.items()
        if isinstance(folder, str) and folder.strip()
    }


def locate(repos: Iterable[str]) -> tuple[dict[str, Checkout], list[str]]:
    """The checkout of each of *repos*, keyed by the name in *repos*, and warnings.

    A ``checkouts`` entry wins when its folder's remote names the repo. A
    missing folder or another remote gives a warning, and the search below
    the scope folders takes over.
    """
    from lore_core.git import current_repo
    from lore_core.system_map_scan import configured_scopes, find_checkouts

    wanted = {r.casefold(): r for r in repos}
    scopes = [(name, folder) for name, folder, _tidy in configured_scopes()]

    def scope_of(path: Path) -> str:
        holding = [(len(f.parts), n) for n, f in scopes if path.resolve().is_relative_to(f)]
        return max(holding)[1] if holding else ""

    found: dict[str, Checkout] = {}
    warnings: list[str] = []
    for slug, folder in pinned_checkouts().items():
        repo = wanted.get(slug.casefold())
        if repo is None:
            continue
        where = f"{CONFIG_KEY}.{slug}: {folder}"
        if not folder.is_dir():
            warnings.append(f"{where} is no folder; searched the scope folders instead")
            continue
        remote = current_repo(folder)
        if (remote or "").casefold() != slug.casefold():
            warnings.append(
                f"{where} has remote {remote or 'none'}, not {slug}; "
                "searched the scope folders instead"
            )
            continue
        found[repo] = Checkout(repo=repo, scope=scope_of(folder), path=folder, source="pinned")
    for scope, folder in scopes:
        if not folder.is_dir():
            continue
        for slug, path in find_checkouts(folder).items():
            repo = wanted.get(slug.casefold())
            if repo and repo not in found:
                found[repo] = Checkout(repo=repo, scope=scope, path=path, source=f"scope {scope}")
    return found, warnings


def target_path(lore_root: Path, system: str) -> Path:
    return Path(lore_root) / ".lore" / TARGET_DIR / f"{system}.json"


def save_target(lore_root: Path, system: str, branch: str, now: datetime) -> Path:
    """Remember *branch* as the target of *system*."""
    from lore_core.io import atomic_write_text

    path = target_path(lore_root, system)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps({"branch": branch, "set": now.date().isoformat()}))
    return path


def load_target(lore_root: Path, system: str) -> dict[str, str] | None:
    """The saved target of *system*: ``branch`` and the date it was ``set``."""
    try:
        data = json.loads(target_path(lore_root, system).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("branch"), str):
        return None
    return {"branch": data["branch"], "set": str(data.get("set") or "")}


def clear_target(lore_root: Path, system: str) -> bool:
    try:
        target_path(lore_root, system).unlink()
    except OSError:
        return False
    return True


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
        source=checkout.source,
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


def fetch(path: Path) -> bool:
    """``git fetch --prune`` in *path*; False when it fails. Reaches the network."""
    try:
        result = subprocess.run(
            ["git", "fetch", "--prune", "--quiet"],
            cwd=str(path),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def report(
    system: SystemMap,
    lore_root: Path,
    branch: str | None = None,
    do_fetch: bool = False,
) -> dict[str, object]:
    """The status of *system*: each checkout producers first, against its target.

    *branch* overrides the saved target for this report; it saves nothing.
    """
    from lore_core.config import user_config_path

    repos = order([system])
    found, warnings = locate(repos)
    saved = load_target(lore_root, system.name)
    target = {"branch": branch, "set": "", "from": "--branch"} if branch else None
    if target is None and saved:
        target = {**saved, "from": "saved"}
    failed = [r for r in repos if r in found and do_fetch and not fetch(found[r].path)]
    states = [state(found[r], target["branch"] if target else None) for r in repos if r in found]
    return {
        "system": system.name,
        "note": str(system.path),
        "target": target,
        "order": repos,
        "checkouts": [s.as_dict() for s in states],
        "at_target": sum(1 for s in states if s.at_target()),
        "no_checkout": [
            {
                "repo": r,
                "hint": f"add `{CONFIG_KEY}: {{{r}: <folder>}}` to {user_config_path()}",
            }
            for r in repos
            if r not in found
        ],
        "pinned": [
            e.as_dict() for e in system.edges if e.pin and e.pin != "none" and e.producer in found
        ],
        "local_work": [
            {"repo": s.repo, "path": s.path, "work": s.local_work()}
            for s in states
            if s.local_work()
        ],
        "fetch_failed": failed,
        "warnings": warnings,
    }
