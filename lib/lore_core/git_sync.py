"""Cross-host sync engine for wiki git repos.

Pure functions over a wiki directory. The hooks/curator wiring layer
calls these; this module never reads the hook event log or spawns
subprocesses outside of git itself.

See `docs/architecture/sync.md` for the conflict policy.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path


class SyncStatus(str, Enum):
    """Outcome of an `auto_pull` or `auto_push` call."""

    OK = "ok"  # change applied (pulled or pushed commits)
    NOOP = "noop"  # nothing to do (clean + already in sync)
    SKIPPED_NO_REMOTE = "no-remote"
    SKIPPED_DIRTY = "dirty"  # uncommitted local changes — refused
    SKIPPED_DIVERGED = "diverged"  # both sides have unique commits
    SKIPPED_UNREACHABLE = "unreachable"
    MERGED = "merged"  # auto_push: took ours on regenerable conflicts
    MERGE_BLOCKED = "merge-blocked"  # auto_push: aborted, user action needed


@dataclass(frozen=True)
class SyncResult:
    """What a sync call did."""

    status: SyncStatus
    message: str = ""
    pulled_commits: int = 0
    pushed_commits: int = 0
    merged_paths: list[str] = field(default_factory=list)
    blocked_paths: list[str] = field(default_factory=list)


class ConflictKind(str, Enum):
    NOTE = "note"  # bail to user
    SESSION = "session"  # bail to user (rare — pre-pull eliminates)
    REGENERABLE = "regenerable"  # ours wins; lint reconciles
    UNKNOWN = "unknown"  # bail to user


_REGENERABLE_FILENAMES = {
    "_catalog.json",
    "_index.txt",
    "_threads.txt",
    "_concepts.txt",
    "_decisions.txt",
    "_recent.txt",
    # Legacy filenames — kept regenerable so a peer pushing from a
    # vault that hasn't run lint after upgrade still auto-resolves.
    # Drop once all clients have re-linted.
    "_index.md",
    "llms.txt",
    "threads.md",
    "_recent.md",
}


# ---------------------------------------------------------------------------
# Git primitives — thin subprocess wrappers
# ---------------------------------------------------------------------------


def _git(
    wiki_dir: Path,
    *args: str,
    check: bool = False,
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    """Run a git subcommand inside ``wiki_dir``.

    Returns the CompletedProcess; callers inspect ``returncode`` and
    ``stdout``/``stderr`` themselves. ``check=True`` raises on non-zero;
    default False so the caller can branch on common "expected failures"
    (push non-FF, fetch unreachable).
    """
    return subprocess.run(
        ["git", *args],
        cwd=str(wiki_dir),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
    )


def _has_remote(wiki_dir: Path) -> bool:
    return _git(wiki_dir, "remote").stdout.strip() != ""


def has_remote(wiki_dir: Path) -> bool:
    """Public: True iff ``wiki_dir`` is a git repo with at least one remote.

    A wiki with a remote is a *shared* vault — content pushed to it is
    visible to every other clone/teammate. Callers outside this module
    (the attach flow's shared-vault consent gate) use this instead of
    reaching into the private ``_has_remote``.
    """
    if not (wiki_dir / ".git").exists():
        return False
    return _has_remote(wiki_dir)


def _is_clean(wiki_dir: Path) -> bool:
    return _git(wiki_dir, "status", "--porcelain").stdout.strip() == ""


def _current_branch(wiki_dir: Path) -> str | None:
    out = _git(wiki_dir, "branch", "--show-current").stdout.strip()
    return out or None


def _upstream_for(wiki_dir: Path, branch: str) -> str | None:
    """Tracking ref for ``branch`` (e.g. ``origin/main``) or None."""
    r = _git(wiki_dir, "rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}")
    return r.stdout.strip() if r.returncode == 0 else None


def is_diverged(wiki_dir: Path) -> bool:
    """True iff the wiki has local commits the remote doesn't AND vice versa.

    Cheap local-only check — never fetches. Useful for ``lore status``
    to surface "wiki diverged" without paying the network cost. Returns
    False (the safe default) for wikis without a remote, without an
    upstream, outside a git repo, or in detached-HEAD state.
    """
    if not (wiki_dir / ".git").exists():
        return False
    if not _has_remote(wiki_dir):
        return False
    branch = _current_branch(wiki_dir)
    if branch is None:
        return False
    upstream = _upstream_for(wiki_dir, branch)
    if upstream is None:
        return False
    ahead, behind = _ahead_behind(wiki_dir, branch, upstream)
    return ahead > 0 and behind > 0


def _ahead_behind(wiki_dir: Path, branch: str, upstream: str) -> tuple[int, int]:
    """Returns (ahead, behind) — local commits not in upstream, and vice versa."""
    r = _git(wiki_dir, "rev-list", "--left-right", "--count", f"{branch}...{upstream}")
    if r.returncode != 0:
        return (0, 0)
    parts = r.stdout.split()
    if len(parts) != 2:
        return (0, 0)
    return (int(parts[0]), int(parts[1]))


# ---------------------------------------------------------------------------
# auto_pull
# ---------------------------------------------------------------------------


def auto_pull(wiki_dir: Path) -> SyncResult:
    """Fetch + fast-forward if safe; skip otherwise.

    Skip rules (in order):
      - no remote configured            → SKIPPED_NO_REMOTE
      - working tree dirty              → SKIPPED_DIRTY
      - fetch fails (network)           → SKIPPED_UNREACHABLE
      - local has commits remote doesn't → SKIPPED_DIVERGED
      - clean tree, FF possible         → OK (or NOOP if already in sync)
    """
    if not (wiki_dir / ".git").exists():
        return SyncResult(status=SyncStatus.SKIPPED_NO_REMOTE, message="no .git")

    if not _has_remote(wiki_dir):
        return SyncResult(status=SyncStatus.SKIPPED_NO_REMOTE)

    if not _is_clean(wiki_dir):
        return SyncResult(
            status=SyncStatus.SKIPPED_DIRTY,
            message="uncommitted changes in working tree",
        )

    branch = _current_branch(wiki_dir)
    if branch is None:
        return SyncResult(status=SyncStatus.SKIPPED_NO_REMOTE, message="detached HEAD")

    fetch = _git(wiki_dir, "fetch", "--quiet")
    if fetch.returncode != 0:
        return SyncResult(
            status=SyncStatus.SKIPPED_UNREACHABLE,
            message=fetch.stderr.strip()[:200],
        )

    upstream = _upstream_for(wiki_dir, branch)
    if upstream is None:
        return SyncResult(status=SyncStatus.SKIPPED_NO_REMOTE, message="no upstream")

    ahead, behind = _ahead_behind(wiki_dir, branch, upstream)
    if behind == 0:
        return SyncResult(status=SyncStatus.NOOP)
    if ahead > 0:
        return SyncResult(
            status=SyncStatus.SKIPPED_DIVERGED,
            message=f"local ahead by {ahead}, remote ahead by {behind}",
        )

    # Pure fast-forward
    ff = _git(wiki_dir, "merge", "--ff-only", upstream)
    if ff.returncode != 0:
        return SyncResult(
            status=SyncStatus.SKIPPED_DIVERGED,
            message=f"ff-only merge refused: {ff.stderr.strip()[:200]}",
        )
    return SyncResult(status=SyncStatus.OK, pulled_commits=behind)


# ---------------------------------------------------------------------------
# auto_push
# ---------------------------------------------------------------------------


def auto_push(
    wiki_dir: Path,
    *,
    note_dirs: list[str] | None = None,
) -> SyncResult:
    """Push local commits, taking ours on regenerable conflicts.

    ``note_dirs`` is the list of subdirectory names whose conflicts count
    as note conflicts (e.g. ``["concepts", "decisions", "results"]``).
    If not given, a permissive default set is used. A note conflict ends
    in MERGE_BLOCKED with the working tree handed back clean; the user
    resolves it with git.

    Returns:
      OK                — push succeeded outright
      NOOP              — nothing to push
      MERGED            — took ours on regenerable conflicts; push succeeded
      MERGE_BLOCKED     — at least one conflict couldn't be auto-resolved;
                          working tree returned to clean (merge --abort)
      other skip codes  — same as auto_pull
    """
    if not (wiki_dir / ".git").exists():
        return SyncResult(status=SyncStatus.SKIPPED_NO_REMOTE, message="no .git")
    if not _has_remote(wiki_dir):
        return SyncResult(status=SyncStatus.SKIPPED_NO_REMOTE)
    branch = _current_branch(wiki_dir)
    if branch is None:
        return SyncResult(status=SyncStatus.SKIPPED_NO_REMOTE, message="detached HEAD")

    # Step 0 — count what's pending before we touch git so we can decide noop vs ok
    upstream_pre = _upstream_for(wiki_dir, branch)
    ahead_pre = _ahead_behind(wiki_dir, branch, upstream_pre)[0] if upstream_pre else 0

    # Step 1 — try the simple FF push first
    push = _git(wiki_dir, "push")
    if push.returncode == 0:
        if ahead_pre == 0:
            return SyncResult(status=SyncStatus.NOOP)
        return SyncResult(status=SyncStatus.OK, pushed_commits=ahead_pre)

    # Step 2 — push failed. Why?
    err = push.stderr.lower()
    if "non-fast-forward" not in err and "rejected" not in err:
        # Some other failure — auth, network, hook rejection
        return SyncResult(
            status=SyncStatus.SKIPPED_UNREACHABLE,
            message=push.stderr.strip()[:200],
        )

    # Step 3 — non-FF: do a real merge so we can inspect conflicts
    fetch = _git(wiki_dir, "fetch", "--quiet")
    if fetch.returncode != 0:
        return SyncResult(
            status=SyncStatus.SKIPPED_UNREACHABLE,
            message=fetch.stderr.strip()[:200],
        )

    upstream = _upstream_for(wiki_dir, branch)
    if upstream is None:
        return SyncResult(status=SyncStatus.SKIPPED_NO_REMOTE)

    merge = _git(wiki_dir, "merge", "--no-commit", "--no-ff", upstream)
    if merge.returncode == 0:
        # Clean merge — no conflicts. Commit + push. If commit fails the
        # working tree is left in a half-merged state (HEAD unchanged,
        # MERGE_HEAD set, index has the merge result), so we explicitly
        # abort before returning.
        commit = _git(wiki_dir, "commit", "--no-edit")
        if commit.returncode != 0 and "nothing to commit" not in commit.stdout.lower():
            _git(wiki_dir, "merge", "--abort")
            return SyncResult(
                status=SyncStatus.MERGE_BLOCKED,
                message=commit.stderr.strip()[:200],
            )
        push2 = _git(wiki_dir, "push")
        if push2.returncode != 0:
            # Merge commit landed locally; only the push half failed.
            # Next auto_push retries via the step-1 fast path.
            return SyncResult(
                status=SyncStatus.SKIPPED_UNREACHABLE,
                message=f"merge committed locally, push failed: {push2.stderr.strip()[:200]}",
            )
        return SyncResult(status=SyncStatus.OK, pushed_commits=1)

    # Step 4 — there are real conflicts. Classify and resolve.
    conflicts = _list_conflicts(wiki_dir)
    if not conflicts:
        # Merge failed but no UNMERGED paths — abort defensively.
        _git(wiki_dir, "merge", "--abort")
        return SyncResult(status=SyncStatus.MERGE_BLOCKED, message="merge failed without conflicts")

    note_names = note_dirs if note_dirs is not None else _note_dirs(wiki_dir)

    merged: list[str] = []
    blocked: list[str] = []
    for path in conflicts:
        kind = _classify_conflict_path(path, note_names)
        if kind is ConflictKind.REGENERABLE:
            # Take ours; lint will truth it after the merge.
            _git(wiki_dir, "checkout", "--ours", path)
            _git(wiki_dir, "add", path)
            merged.append(path)
            continue
        # NOTE, SESSION, UNKNOWN
        blocked.append(path)

    if blocked:
        _git(wiki_dir, "merge", "--abort")
        return SyncResult(
            status=SyncStatus.MERGE_BLOCKED,
            blocked_paths=blocked,
            merged_paths=merged,  # what we'd-have-merged but discarded on abort
            message=f"{len(blocked)} unresolved conflict(s)",
        )

    commit = _git(
        wiki_dir,
        "commit",
        "-m",
        f"merge(auto): {len(merged)} regenerable file(s)" if merged else "merge",
    )
    if commit.returncode != 0:
        _git(wiki_dir, "merge", "--abort")
        return SyncResult(
            status=SyncStatus.MERGE_BLOCKED,
            message=commit.stderr.strip()[:200],
        )
    push3 = _git(wiki_dir, "push")
    if push3.returncode != 0:
        # Merge commit landed locally; push failed (network, auth, etc.).
        # Next auto_push retries via the step-1 fast path.
        return SyncResult(
            status=SyncStatus.SKIPPED_UNREACHABLE,
            merged_paths=merged,
            message=f"merge-commit landed locally, push failed: {push3.stderr.strip()[:200]}",
        )
    return SyncResult(
        status=SyncStatus.MERGED,
        merged_paths=merged,
        pushed_commits=1,
    )


# ---------------------------------------------------------------------------
# Conflict classification
# ---------------------------------------------------------------------------


def _list_conflicts(wiki_dir: Path) -> list[str]:
    """Paths in the unmerged state, relative to wiki_dir."""
    r = _git(wiki_dir, "diff", "--name-only", "--diff-filter=U")
    return [line for line in r.stdout.splitlines() if line.strip()]


def _note_dirs(wiki_dir: Path) -> list[str]:
    """Return the subdirectory names classified as notes.

    Used only to classify a conflict path; ``wiki_dir`` is unused but
    kept for call-site stability.
    """
    return ["concepts", "decisions", "results", "people", "places", "questions"]


def _classify_conflict_path(path: str, note_dirs: list[str]) -> ConflictKind:
    """Classify a conflict path into one of the four resolution buckets."""
    parts = path.split("/")
    name = parts[-1]
    if name in _REGENERABLE_FILENAMES:
        return ConflictKind.REGENERABLE

    # wiki/<wiki>/<note_dir>/*.md  OR  <note_dir>/*.md (when path
    # is already wiki-root-relative)
    if any(seg in note_dirs for seg in parts):
        return ConflictKind.NOTE

    if "sessions" in parts:
        return ConflictKind.SESSION

    return ConflictKind.UNKNOWN


# ---------------------------------------------------------------------------
# Wiki connection health (issue #193) — one FAST local-first snapshot per wiki
# for the `lore status` dashboard. Everything except reachability is a cheap
# local git query; the network probe is opt-in (skipped under `offline`) and
# time-boxed so a dead remote never stalls the dashboard.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WikiHealth:
    is_repo: bool
    has_remote: bool
    branch: str | None
    dirty: bool
    ahead: int
    behind: int
    reachable: bool | None  # None = not probed (offline, or no remote)
    last_sync: datetime | None  # FETCH_HEAD mtime, best-effort


def wiki_health(wiki_dir: Path, *, offline: bool = False, timeout: int = 3) -> WikiHealth:
    """Snapshot one wiki's git connection health.

    Local queries (branch, dirty, ahead/behind vs the last-known upstream)
    never touch the network. Reachability runs ``git ls-remote`` behind a
    short ``timeout`` and only when a remote exists and ``offline`` is False;
    a timeout or error reads as unreachable, never a raise.
    """
    if not (wiki_dir / ".git").exists():
        return WikiHealth(False, False, None, False, 0, 0, None, None)

    has_rem = _has_remote(wiki_dir)
    branch = _current_branch(wiki_dir)
    dirty = not _is_clean(wiki_dir)
    ahead = behind = 0
    reachable: bool | None = None
    if has_rem and branch is not None:
        upstream = _upstream_for(wiki_dir, branch)
        if upstream is not None:
            ahead, behind = _ahead_behind(wiki_dir, branch, upstream)
        if not offline:
            reachable = _remote_reachable(wiki_dir, timeout=timeout)
    return WikiHealth(
        is_repo=True,
        has_remote=has_rem,
        branch=branch,
        dirty=dirty,
        ahead=ahead,
        behind=behind,
        reachable=reachable,
        last_sync=_last_fetch_time(wiki_dir),
    )


def _remote_reachable(wiki_dir: Path, *, timeout: int) -> bool:
    remotes = _git(wiki_dir, "remote").stdout.split()
    if not remotes:
        return False
    try:
        r = _git(wiki_dir, "ls-remote", "--quiet", remotes[0], timeout=timeout)
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError):
        return False
    return r.returncode == 0


def _last_fetch_time(wiki_dir: Path) -> datetime | None:
    fetch_head = wiki_dir / ".git" / "FETCH_HEAD"
    try:
        return datetime.fromtimestamp(fetch_head.stat().st_mtime, tz=UTC)
    except OSError:
        return None
