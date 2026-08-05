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
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
    roots = {p for p in (_sessions_root_of(lore_root, d) for d in plan.deletions) if p}
    for path in plan.deletions:
        try:
            _assert_deletable(lore_root, path)
            path.unlink()
            deleted.append(path)
        except (OSError, ValueError) as exc:
            failed.append((path, str(exc)))

    for root in roots:
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


def _wiki_dirs(lore_root: Path, wiki: str | None) -> list[Path]:
    root = lore_root / "wiki"
    if not root.is_dir():
        return []
    if wiki:
        target = root / wiki
        return [target] if target.is_dir() else []
    return sorted(d for d in root.iterdir() if d.is_dir())


def _iter_session_notes(lore_root: Path, wiki: str | None):
    for wiki_dir in _wiki_dirs(lore_root, wiki):
        sessions = wiki_dir / "sessions"
        if not sessions.is_dir():
            continue
        for path in sessions.rglob("*.md"):
            if path.is_file() and not path.is_symlink():
                yield path


def _iter_non_notes(lore_root: Path, wiki: str | None):
    for wiki_dir in _wiki_dirs(lore_root, wiki):
        sessions = wiki_dir / "sessions"
        if not sessions.is_dir():
            continue
        for path in sessions.rglob("*"):
            if path.is_file() and path.suffix != ".md":
                yield path


def _sessions_root_of(lore_root: Path, path: Path) -> Path | None:
    for parent in path.parents:
        if parent.name == "sessions" and parent.parent.parent == lore_root / "wiki":
            return parent
    return None


def _assert_deletable(lore_root: Path, path: Path) -> None:
    """Refuse anything that is not a plain ``.md`` file inside a
    ``<lore_root>/wiki/<name>/sessions/`` tree.

    The plan is computed from the same walk, so this can only fire if the
    tree changed under us or a caller hand-built a plan — which is
    exactly when a delete loop must stop rather than guess.
    """
    if path.suffix != ".md":
        raise ValueError(f"not a session note: {path}")
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"not a regular file: {path}")
    inside = _sessions_root_of(lore_root, path.resolve()) or _sessions_root_of(lore_root, path)
    if inside is None:
        raise ValueError(f"outside any wiki sessions tree: {path}")


def _prune_empty_dirs(root: Path) -> None:
    """Remove directories left empty under ``root``, deepest first."""
    if not root.is_dir():
        return
    for directory in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if directory.is_dir():
            with contextlib.suppress(OSError):
                directory.rmdir()
    with contextlib.suppress(OSError):
        root.rmdir()
