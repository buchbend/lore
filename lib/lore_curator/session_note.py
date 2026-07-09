"""Ensure the append-only session note_document note for a buffer exists.

The buffer-and-flush heartbeat no longer writes a live preview note. It
guarantees the session's note file exists — the fixed machine-written
genre disclaimer plus machine-first frontmatter, with zero chapters — and
records the path on the buffer sidecar (``stub_path``). The body only
ever grows by *chapters*, one per flush, written by the flush lifecycle;
nothing here rewrites a body. The note is append-only until close.

Path selection reuses the deterministic slug + canonical session path
helpers so a note keeps the same filename it would have had before,
regardless of which layer first creates it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from lore_core import note_document as nd
from lore_core.linkage import Linkage, extract_linkage
from lore_core.note_document import SessionFacts
from lore_core.types import Scope, TranscriptHandle

from lore_curator.buffer_append import AppendOutcome
from lore_curator.buffer_store import Buffer, ReplayedBuffer, Sidecar
from lore_curator.session_activity import collect_commits_by_sha
from lore_curator.stub_note import (
    _claim_first_write_slot,
    _derive_slug,
    _placeholder_title,
)

if TYPE_CHECKING:
    from lore_core.run_log import RunLogger


__all__ = [
    "EnsureResult",
    "ensure_note",
    "ensure_note_from_sidecar",
    "facts_from_replay",
    "linkage_from_replay",
]


@dataclass
class EnsureResult:
    """What :func:`ensure_note` reports back to the heartbeat caller."""

    path: Path
    wikilink: str
    is_first_write: bool


def facts_from_replay(rb: ReplayedBuffer, *, duration_seconds: int = 0) -> SessionFacts:
    """Build the deterministic session facts snapshot from a replayed buffer.

    Facts only ever grow, so this cumulative snapshot is safe to hand to
    ``create_note`` / ``append_chapter`` on every write — an empty field
    never blanks a value already recorded.
    """
    return SessionFacts(
        commits=list(rb.commit_shas),
        files_modified=list(rb.files_modified),
        files_read=list(rb.files_read),
        projects=list(rb.projects),
        duration_seconds=duration_seconds,
    )


def linkage_from_replay(
    rb: ReplayedBuffer,
    *,
    cwd: str,
    wiki_root: Path,
    handle: str,
) -> Linkage:
    """Build the deterministic linkage snapshot from a replayed buffer.

    Resolves commit subject/body text via ``collect_commits_by_sha`` (this
    module's own curator layer), then hands off to
    ``lore_core.linkage.extract_linkage`` for repo/branch/ref classification —
    keeping that module free of any curator-layer import.
    """
    repo_root = Path(cwd) if cwd else None
    commits = collect_commits_by_sha(repo_root, list(rb.commit_shas))
    commit_texts = [f"{c.subject}\n{c.body}".strip() for c in commits]
    return extract_linkage(
        cwd=cwd or None,
        commit_texts=commit_texts,
        wiki_root=wiki_root,
        handle=handle,
    )


def ensure_note(
    *,
    outcome: AppendOutcome,
    scope: Scope,
    transcript: TranscriptHandle,
    wiki_root: Path,
    work_time: datetime,
    handle_label: str = "",
    integration: str = "",
    logger: RunLogger | None = None,
) -> EnsureResult | None:
    """Guarantee the session note exists; return its path (or ``None``).

    First heartbeat: derive the canonical path, create the note (disclaimer
    + machine-first frontmatter + a cumulative facts snapshot), and stamp
    ``stub_path`` on the sidecar. Later heartbeats: return the recorded
    path unchanged — the note is append-only and only the flush touches its
    body. A no-op heartbeat (empty chunk) returns ``None``.
    """
    if outcome.skipped_no_op:
        return None
    buffer = outcome.buffer
    sidecar = outcome.sidecar_after or buffer.read_sidecar()
    if sidecar is None:
        return None

    if sidecar.stub_path:
        path = Path(sidecar.stub_path)
        return EnsureResult(path=path, wikilink=f"[[{path.stem}]]", is_first_write=False)

    rb = buffer.replay()
    slug = _derive_slug(
        activity=outcome.activity,
        files_touched=outcome.files_touched,
        scope=scope,
        work_time=work_time,
    )

    def _write(candidate: Path) -> None:
        nd.create_note(
            candidate,
            title=_placeholder_title(scope, work_time),
            description="Lab-notebook session note.",
            scope=scope.scope,
            handle=handle_label or None,
            created=work_time.date().isoformat(),
            facts=facts_from_replay(rb),
            linkage=linkage_from_replay(
                rb, cwd=sidecar.cwd, wiki_root=wiki_root, handle=handle_label
            ),
            extra_frontmatter={
                "transcript_id": sidecar.transcript_id or transcript.id,
                "integration": integration or transcript.integration,
                "buffer_stem": buffer.stem,
            },
            wiki_root=wiki_root,
            exclusive=True,
        )

    path = _claim_first_write_slot(
        wiki_root=wiki_root,
        handle_label=handle_label,
        work_time=work_time,
        slug=slug,
        write_fn=_write,
    )
    with buffer.with_lock():
        buffer.patch(stub_path=str(path))

    wikilink = f"[[{path.stem}]]"
    if logger is not None:
        logger.emit(
            "session-note-created",
            transcript_id=sidecar.transcript_id,
            path=str(path),
            wikilink=wikilink,
            buffer_stem=buffer.stem,
        )
    return EnsureResult(path=path, wikilink=wikilink, is_first_write=True)


def _work_time_from_sidecar(sidecar: Sidecar) -> datetime:
    """Best-effort local wall-clock the note filename should date to."""
    for stamp in (sidecar.created_at, sidecar.last_appended_at):
        if not stamp:
            continue
        try:
            dt = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone()
    return datetime.now().astimezone()


def ensure_note_from_sidecar(
    buffer: Buffer,
    sidecar: Sidecar,
    rb: ReplayedBuffer,
    *,
    wiki_root: Path,
    logger: RunLogger | None = None,
) -> Path:
    """Return the session-note path, creating it from the sidecar if absent.

    The flush and the startup sweep have only a buffer + its sidecar (no
    live heartbeat context), so this rebuilds the deterministic path and
    creates the note when the heartbeat never got to. Idempotent: an
    already-recorded ``stub_path`` is returned untouched. The caller must
    hold the buffer flock (this patches ``stub_path``).
    """
    if sidecar.stub_path:
        return Path(sidecar.stub_path)

    work_time = _work_time_from_sidecar(sidecar)
    scope = Scope(
        wiki=sidecar.wiki,
        scope=sidecar.scope,
        backend="none",
        claude_md_path=Path(sidecar.cwd or "."),
    )
    slug = _derive_slug(
        activity={"commits": rb.activity_commits},
        files_touched=rb.files_touched,
        scope=scope,
        work_time=work_time,
    )

    def _write(candidate: Path) -> None:
        nd.create_note(
            candidate,
            title=_placeholder_title(scope, work_time),
            description="Lab-notebook session note.",
            scope=sidecar.scope,
            handle=sidecar.handle or None,
            created=work_time.date().isoformat(),
            facts=facts_from_replay(rb),
            linkage=linkage_from_replay(
                rb, cwd=sidecar.cwd, wiki_root=wiki_root, handle=sidecar.handle
            ),
            extra_frontmatter={
                "transcript_id": sidecar.transcript_id,
                "integration": sidecar.integration,
                "buffer_stem": buffer.stem,
            },
            wiki_root=wiki_root,
            exclusive=True,
        )

    path = _claim_first_write_slot(
        wiki_root=wiki_root,
        handle_label=sidecar.handle,
        work_time=work_time,
        slug=slug,
        write_fn=_write,
    )
    buffer.patch(stub_path=str(path))
    if logger is not None:
        logger.emit(
            "session-note-created",
            transcript_id=sidecar.transcript_id,
            path=str(path),
            wikilink=f"[[{path.stem}]]",
            buffer_stem=buffer.stem,
        )
    return path
