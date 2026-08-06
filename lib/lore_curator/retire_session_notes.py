"""Retire the session-note stock into the transcript ledger.

Backs the `lore migrate retire-session-notes` verb. Two halves, in this
order:

1. **Backfill** — derive a linkage block for every archived transcript
   and write it onto its ledger entry. Sources are the transcript itself
   (edited files, commit SHAs, refs in the turn text, and the branch the
   session ran on) plus git (the repo the cwd resolves to). No LLM call
   and no network call: an old note's prose is not distilled into the
   ledger, and a bare ``#42`` is classified by syntax rather than by
   asking GitHub what kind of ref it is. Backfilling 500-plus sessions
   would otherwise mean a request per ref inside a command that then
   deletes files.
2. **Delete** — remove the session-note markdown under each wiki's
   ``sessions/`` tree, then prune the directories that empty out.

The plan is the contract. :func:`plan_retirement` reads and computes but
writes nothing; :func:`apply_retirement` executes exactly the plan it is
handed, so a dry run and a real run can be compared file by file.

The deletion is final. Capture writes no session note, so nothing refills
the stock; this verb clears what earlier releases left behind and is a
one-shot for anyone upgrading past the teardown.
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lore_core.config import list_wikis

__all__ = [
    "BackfillItem",
    "RetirementPlan",
    "RetirementReport",
    "apply_retirement",
    "plan_retirement",
]


@dataclass(frozen=True)
class BackfillItem:
    """One ledger entry and the linkage block to stamp on it."""

    integration: str
    transcript_id: str
    linkage: dict[str, Any]
    #: Set when the transcript file is gone; the block is then whatever
    #: git and the stored cwd could still answer.
    transcript_missing: bool = False


@dataclass(frozen=True)
class RetirementPlan:
    """Everything the command would do. Produced without writing."""

    backfill: list[BackfillItem] = field(default_factory=list)
    #: Session-note files to delete, sorted by path.
    deletions: list[Path] = field(default_factory=list)
    #: Non-markdown files found under a ``sessions/`` tree. Left in place.
    kept: list[Path] = field(default_factory=list)


@dataclass(frozen=True)
class RetirementReport:
    backfilled: int
    deleted: int
    deleted_paths: list[Path]
    failed: list[tuple[Path, str]] = field(default_factory=list)


def plan_retirement(lore_root: Path, *, wiki: str | None = None) -> RetirementPlan:
    """Compute the backfill and deletion plan. Writes nothing."""
    return RetirementPlan(
        backfill=_plan_backfill(lore_root),
        deletions=sorted(_iter_session_notes(lore_root, wiki)),
        kept=sorted(_iter_non_notes(lore_root, wiki)),
    )


def apply_retirement(lore_root: Path, plan: RetirementPlan) -> RetirementReport:
    """Execute ``plan``: stamp the linkage blocks, then delete the notes.

    Backfill runs first so a crash mid-delete still leaves the ledger
    holding what the notes were about.
    """
    from lore_core.ledger import TranscriptLedger

    ledger = TranscriptLedger(lore_root)
    updated = []
    for item in plan.backfill:
        entry = ledger.get(item.integration, item.transcript_id)
        if entry is None:
            continue
        entry.linkage = {**entry.linkage, **item.linkage}
        updated.append(entry)
    ledger.bulk_upsert(updated)

    deleted: list[Path] = []
    failed: list[tuple[Path, str]] = []
    # Every wiki's roots, not just the planned wiki's: the guard needs
    # the legitimate universe, and a scoped plan only holds its own paths.
    allowed = _sessions_roots(lore_root)
    prune_roots = {p for p in (_sessions_root_of(lore_root, d) for d in plan.deletions) if p}
    for path in plan.deletions:
        try:
            _assert_deletable(allowed, path)
            path.unlink()
            deleted.append(path)
        except (OSError, ValueError) as exc:
            failed.append((path, str(exc)))

    for root in prune_roots:
        _prune_empty_dirs(root)

    return RetirementReport(
        backfilled=len(updated),
        deleted=len(deleted),
        deleted_paths=deleted,
        failed=failed,
    )


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------


def _plan_backfill(lore_root: Path) -> list[BackfillItem]:
    from lore_adapters import get_adapter
    from lore_core.ledger import TranscriptLedger
    from lore_core.types import TranscriptHandle

    from lore_curator.ledger_linkage import build_linkage

    items: list[BackfillItem] = []
    for entry in TranscriptLedger(lore_root).all_entries():
        turns = None
        if entry.path.exists():
            try:
                adapter = get_adapter(entry.integration)
                handle = TranscriptHandle(
                    integration=entry.integration,
                    id=entry.transcript_id,
                    path=entry.path,
                    cwd=entry.directory,
                    mtime=entry.last_mtime,
                )
                turns = list(adapter.read_slice(handle, 0))
            except Exception:  # noqa: BLE001 - one unreadable archive must not stop the migration
                turns = None
        items.append(
            BackfillItem(
                integration=entry.integration,
                transcript_id=entry.transcript_id,
                linkage=build_linkage(
                    entry.directory,
                    turns=turns,
                    branch=_recorded_branch(entry.path),
                ),
                transcript_missing=turns is None,
            )
        )
    return items


def _recorded_branch(transcript: Path) -> str | None:
    """The branch the session ran on, as the integration recorded it.

    Claude Code stamps ``gitBranch`` on each transcript line. That is the
    only honest source for an archived session: the checkout has moved on
    since, so asking git today would attribute old work to today's
    branch. Returns ``None`` when the field is absent, which leaves the
    git-derived branch in place.
    """
    try:
        with transcript.open("r", encoding="utf-8", errors="replace") as fp:
            for raw in fp:
                try:
                    branch = json.loads(raw).get("gitBranch")
                except (json.JSONDecodeError, AttributeError):
                    continue
                if isinstance(branch, str) and branch:
                    return branch
    except OSError:
        return None
    return None


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------


def _selected_wikis(lore_root: Path, wiki: str | None) -> list[Path]:
    dirs = list_wikis(lore_root)
    if wiki:
        return [d for d in dirs if d.name == wiki]
    return dirs


def _iter_sessions_dirs(lore_root: Path, wiki: str | None):
    """Yield each wiki's ``sessions/`` directory as the vault names it.

    Unresolved on purpose: the plan should print the path the owner
    knows. :func:`_contained` resolves each candidate before it is
    allowed into the plan.
    """
    for wiki_dir in _selected_wikis(lore_root, wiki):
        sessions = wiki_dir / "sessions"
        if sessions.is_dir() and not sessions.is_symlink():
            yield sessions


def _iter_session_notes(lore_root: Path, wiki: str | None):
    roots = _sessions_roots(lore_root, wiki)
    for sessions in _iter_sessions_dirs(lore_root, wiki):
        for path in sessions.rglob("*.md"):
            if path.is_file() and not path.is_symlink() and _contained(roots, path):
                yield path


def _iter_non_notes(lore_root: Path, wiki: str | None):
    roots = _sessions_roots(lore_root, wiki)
    for sessions in _iter_sessions_dirs(lore_root, wiki):
        for path in sessions.rglob("*"):
            if path.is_file() and path.suffix != ".md" and _contained(roots, path):
                yield path


def _sessions_root_of(lore_root: Path, path: Path) -> Path | None:
    """The in-vault ``sessions/`` directory above ``path``, lexically.

    Used only to pick which directory to tidy after a delete —
    containment is :func:`_contained`'s job, on resolved paths.
    """
    for parent in path.parents:
        if parent.name == "sessions" and parent.parent.parent == lore_root / "wiki":
            return parent
    return None


def _sessions_roots(lore_root: Path, wiki: str | None = None) -> list[Path]:
    """Every wiki's real ``sessions/`` directory, resolved.

    Containment is measured against the resolved *wiki's* sessions tree,
    not against the vault root. A wiki is routinely a symlink into its
    own git repo — wikis are portable units — so resolving against the
    root instead drops every symlinked wiki from the plan without a
    word.

    A ``sessions`` entry that is itself a symlink is skipped. That one
    points the tree out of its own wiki, which is the escape rather than
    the layout: it let a plan list a link target's files under
    in-vault-looking paths, and ``--apply`` delete them.
    """
    roots: list[Path] = []
    for wiki_dir in _selected_wikis(lore_root, wiki):
        sessions = wiki_dir / "sessions"
        if sessions.is_dir() and not sessions.is_symlink():
            roots.append(sessions.resolve())
    return roots


def _contained(roots: list[Path], path: Path) -> bool:
    """True when ``path`` resolves inside one of ``roots``.

    Resolving the candidate is the whole guard: ``sessions/../../../x``
    and a symlinked shard below ``sessions/`` both land outside every
    root and are refused. Comparing an unresolved path — or falling back
    to a lexical check when the resolved one misses — re-admits exactly
    those two.
    """
    target = path.resolve()
    return any(target.is_relative_to(root) for root in roots)


def _assert_deletable(roots: list[Path], path: Path) -> None:
    """Refuse anything that is not a plain ``.md`` file inside ``roots``.

    The plan is computed from the same roots and the same predicate, so
    this can only fire if the tree changed under us or a caller
    hand-built a plan — which is exactly when a delete loop must stop
    rather than guess.
    """
    if path.suffix != ".md":
        raise ValueError(f"not a session note: {path}")
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"not a regular file: {path}")
    if not _contained(roots, path):
        raise ValueError(f"outside any wiki sessions tree: {path}")


def _prune_empty_dirs(root: Path) -> None:
    """Remove directories left empty under ``root``, deepest first.

    ``os.walk(followlinks=False)`` rather than ``rglob``: rglob descends
    through a directory symlink, which would let the prune rmdir empty
    directories outside the vault.
    """
    if not root.is_dir() or root.is_symlink():
        return
    for dirpath, _dirnames, _filenames in os.walk(root, topdown=False, followlinks=False):
        with contextlib.suppress(OSError):
            Path(dirpath).rmdir()
