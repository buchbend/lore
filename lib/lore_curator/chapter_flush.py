"""Chapter flush lifecycle for the buffer-and-flush curator.

One flush turns the buffer's unflushed transcript slice into exactly one
chapter of the session's append-only note:

    read note-so-far + slice  ->  compose_chapter (one LLM call, bounded
    retry)  ->  publish gate  ->  append_chapter | withheld marker +
    quarantine | failed marker

The gate is the real :class:`~lore_core.publish_gate.PublishGate`; the
text it scans is exactly ``render_chapter_body(chapter)`` — the same
bytes that land in the note. The composer already retries twice against
the gate internally, so a gate withhold that reaches this layer is
terminal (marker + quarantine).

Failure semantics
-----------------
* **Mid-session (in-place) failure is silent.** No marker is written
  while a retry chance remains: the buffer stays ``accumulating`` and the
  next trigger retries with the grown slice. A failed attempt is only
  remembered (``flush_attempts``) so the give-up bound can fire.
* **Give-up bound.** A buffer with a prior failed attempt that has grown
  to 2x the cap gets a deterministic *failed* marker chapter for the
  span and a fresh buffer (log + counters reset) — the note stays open;
  one session is still one note.
* **Session-end (close) failure** writes the failed marker and closes the
  note. Composed / withheld close paths append their chapter / marker and
  close all the same. Either way a closed session note is immutable.

No note is better than a noise note
-----------------------------------
* A **trivial session** (tiny turn count, no file/commit/issue activity,
  no chapters yet) is discarded deterministically at close: the stub
  note is removed, no LLM call is spent.
* A compose that returns **zero blocks** is the model's "nothing of
  substance" answer (``EMPTY``). In place, the judged span is consumed
  (buffer reset) so it is never recomposed; at close, a note that never
  gained a chapter is removed instead of being closed empty.

Markers are terminal-only. Flush triggers (cap 120 turns / 240K chars,
pre-compact, session-end, reaper) are unchanged and live elsewhere; this
module consumes a request and drives the composer + gate + note.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lore_adapters import Adapter, get_adapter
from lore_core import note_document as nd
from lore_core import publish_gate as pg
from lore_core.flush_store import FlushState, FlushStore
from lore_core.lockfile import LockContendedError, curator_lock
from lore_core.publish_gate import GateResult, PublishGate
from lore_core.session_writer import FiledNote
from lore_core.spine import ErrorCode, SpineWriter, new_trace_id
from lore_core.types import TranscriptHandle, Turn

from lore_curator._auto_commit import maybe_auto_commit
from lore_curator.buffer_store import (
    Buffer,
    BufferTransitionError,
    Counters,
    ReplayedBuffer,
    Sidecar,
    iter_all,
)
from lore_curator.chapter_compose import ComposeStatus, Gate, compose_chapter
from lore_curator.session_filer import _slug
from lore_curator.session_note import (
    ensure_note_from_sidecar,
    facts_from_replay,
    linkage_from_replay,
)
from lore_curator.stub_note import _lead_for_rename, _resolve_renamed_path, _topic_title

if TYPE_CHECKING:
    from lore_core.run_log import RunLogger


__all__ = [
    "FlushOutcome",
    "SweepReport",
    "flush_chapter",
    "synth_and_close",
    "synth_in_place",
    "spawn_detached_flush",
    "sweep_dead_sessions",
    "startup_sweep",
    "FLUSH_DEFAULT_CAP_TURNS",
    "FLUSH_DEFAULT_CAP_CHARS",
]


# Fallbacks when a wiki config can't be loaded — mirror the WikiConfig
# curator defaults. The give-up bound is 2x these.
FLUSH_DEFAULT_CAP_TURNS = 120
FLUSH_DEFAULT_CAP_CHARS = 240_000

# Trivial-session gate: a closing session at or below BOTH bounds, with
# zero repo activity and no chapters yet, leaves no note at all. Kept
# deliberately tight — a short but substantive exchange easily exceeds
# the char bound, and anything bigger still gets the composer, which can
# answer "nothing of substance" itself.
TRIVIAL_MAX_TURNS = 8
TRIVIAL_MAX_CHARS = 4_000

# Startup-sweep bounds. A dead buffer older than SWEEP_STALE_DAYS is closed
# with a marker and no LLM call (its transcript is likely gone); at most
# SWEEP_MAX_COMPOSE recent dead buffers are composed per sweep so a deep
# backlog can never stall SessionStart.
SWEEP_STALE_DAYS = 3
SWEEP_MAX_COMPOSE = 8


@dataclass
class FlushOutcome:
    """Terminal report of one chapter flush.

    ``status`` names the branch taken:
    ``composed`` | ``empty`` | ``trivial`` | ``withheld`` | ``failed`` |
    ``deferred`` | ``gave-up`` | ``closed-empty`` | ``skipped``. On
    ``discarded`` outcomes (``trivial``, or ``empty`` at close with no
    chapters) the stub note was deleted; ``note_path`` still names the
    removed file so the deletion can be committed. The ``phase*`` /
    ``degraded`` fields are compatibility shims the CLI + dispatch
    telemetry still read.
    """

    buffer_stem: str
    state_before: str = ""
    status: str = ""
    skipped_reason: str = ""
    note_path: Path | None = None
    wikilink: str = ""
    chapter_n: int = 0
    attempts: int = 0
    closed: bool = False
    discarded: bool = False

    # --- compatibility shims (CLI print + flush-dispatch telemetry) ---
    @property
    def phase1_completed(self) -> bool:
        return self.note_path is not None

    @property
    def phase2_completed(self) -> bool:
        return self.status == "composed"

    @property
    def degraded(self) -> bool:
        return self.status in ("failed", "deferred", "gave-up")

    @property
    def phase2_attempts(self) -> int:
        return self.attempts

    @property
    def stub_path(self) -> Path | None:
        return self.note_path


# ---------------------------------------------------------------------------
# Cap + slice helpers
# ---------------------------------------------------------------------------


def _flushed_to(note_path: Path) -> int:
    """Highest turn already covered by a chapter (topic or marker), or -1."""
    try:
        view = nd.read_note(note_path)
    except OSError:
        return -1
    return max((int(c.get("to_turn", -1)) for c in view.chapters), default=-1)


def _read_buffered_turns(*, sidecar: Sidecar, rb: ReplayedBuffer, adapter_lookup) -> list[Turn]:
    """Reconstruct the turns the buffer's slice pointers reach, best-effort.

    Returns an empty list when the adapter can't be loaded or the
    transcript file is gone — the flush treats that like a compose
    failure so the deferred / marker path takes over.
    """
    if not rb.slices:
        return []
    try:
        adapter: Adapter = adapter_lookup(sidecar.integration)
    except Exception:  # noqa: BLE001
        return []
    path_attr = getattr(adapter, "transcript_path_for_id", None)
    if not callable(path_attr):
        return []
    try:
        tx_path = path_attr(sidecar.transcript_id, Path(sidecar.cwd))
    except Exception:  # noqa: BLE001
        return []
    if tx_path is None:
        return []
    handle = TranscriptHandle(
        integration=sidecar.integration,
        id=sidecar.transcript_id,
        path=tx_path,
        cwd=Path(sidecar.cwd),
        mtime=datetime.now(UTC),
    )
    first_idx = min(s.from_index for s in rb.slices)
    last_idx = max(s.to_index for s in rb.slices)
    out: list[Turn] = []
    try:
        for turn in adapter.read_slice(handle, from_index=first_idx):
            if turn.index > last_idx:
                break
            out.append(turn)
    except Exception:  # noqa: BLE001 - never crash a flush on an adapter failure
        return out
    return out


def _slice_text(turns: list[Turn]) -> str:
    return "\n".join(f"[{t.role}@{t.index}] {t.text}" for t in turns if t.text)


# ---------------------------------------------------------------------------
# The flush
# ---------------------------------------------------------------------------


def synth_and_close(
    buffer_path: Path,
    *,
    lore_root: Path,
    wiki_root: Path,
    llm_client: Any = None,
    model: str | None = None,
    adapter_lookup=None,
    logger: RunLogger | None = None,
    auto_commit: bool = True,
    gate: Gate | None = None,
) -> FlushOutcome:
    """Flush one chapter, then close the note and archive the buffer.

    Cap-trip does *not* use this (it flushes in place); the reaper and the
    startup sweep do, plus any caller that has decided the session is over.
    A composed chapter, a withheld marker, or a failed marker is written —
    either way the note ends closed and immutable.
    """
    return flush_chapter(
        buffer_path,
        lore_root=lore_root,
        wiki_root=wiki_root,
        llm_client=llm_client,
        model=model,
        adapter_lookup=adapter_lookup,
        logger=logger,
        auto_commit=auto_commit,
        close=True,
        gate=gate,
    )


def synth_in_place(
    buffer_path: Path,
    *,
    lore_root: Path,
    wiki_root: Path,
    llm_client: Any = None,
    model: str | None = None,
    adapter_lookup=None,
    logger: RunLogger | None = None,
    auto_commit: bool = True,
    gate: Gate | None = None,
) -> FlushOutcome:
    """Flush one chapter against the live buffer and keep it accumulating.

    Cap-trip, pre-compact, and session-end fire this so one session yields
    one growing note. On compose success a chapter is appended; on a gate
    withhold a marker + quarantine entry are written; on compose failure
    the flush defers silently (no marker) unless the give-up bound trips.
    """
    return flush_chapter(
        buffer_path,
        lore_root=lore_root,
        wiki_root=wiki_root,
        llm_client=llm_client,
        model=model,
        adapter_lookup=adapter_lookup,
        logger=logger,
        auto_commit=auto_commit,
        close=False,
        gate=gate,
    )


def flush_chapter(
    buffer_path: Path,
    *,
    lore_root: Path,
    wiki_root: Path,
    llm_client: Any = None,
    model: str | None = None,
    adapter_lookup=None,
    logger: RunLogger | None = None,
    auto_commit: bool = True,
    close: bool,
    gate: Gate | None = None,
) -> FlushOutcome:
    """Compose one chapter and fold it into the session note.

    See the module docstring for the compose -> gate -> append lifecycle
    and the defer / give-up / close failure semantics.
    """
    adapter_lookup = adapter_lookup or get_adapter
    gate = gate or PublishGate()

    buffer = Buffer.from_sidecar_path(buffer_path)
    sidecar = buffer.read_sidecar()
    if sidecar is None:
        # A sidecar that exists but won't parse is a read *error*, not a
        # missing buffer — surface it loudly instead of skipping in silence.
        if buffer.sidecar_path.exists():
            SpineWriter(lore_root).emit(
                source="curator",
                event="flush-skipped",
                level="error",
                error_code=ErrorCode.SIDECAR_READ_FAILED,
                data={"buffer_stem": buffer.stem, "reason": "sidecar-unreadable"},
            )
        return FlushOutcome(buffer_stem=buffer.stem, status="skipped", skipped_reason="no-sidecar")
    if sidecar.state == "closed":
        return FlushOutcome(
            buffer_stem=buffer.stem,
            state_before="closed",
            status="skipped",
            skipped_reason="already-closed",
        )

    # ---- Snapshot under the flock: note path, note-so-far, slice bounds -
    with buffer.with_lock():
        sidecar = buffer.read_sidecar()
        if sidecar is None or sidecar.state == "closed":
            return FlushOutcome(
                buffer_stem=buffer.stem,
                state_before="closed",
                status="skipped",
                skipped_reason="closed-during-acquire",
            )
        state_before = sidecar.state
        if close:
            if sidecar.state == "accumulating":
                sidecar = buffer.transition("ready")
            if sidecar.state == "ready":
                sidecar = buffer.transition("flushing")
        rb = buffer.replay()
        note_path = ensure_note_from_sidecar(
            buffer,
            sidecar,
            rb,
            wiki_root=wiki_root,
            logger=logger,
        )
        note_so_far = _read_note_body(note_path)
        flushed_to = _flushed_to(note_path)
        last_idx = max((s.to_index for s in rb.slices), default=-1)

    # ---- Trivial-session gate (close only; deterministic, no LLM) -------
    if close and _is_trivial(rb, flushed_to):
        with buffer.with_lock():
            with contextlib.suppress(OSError):
                note_path.unlink()
            buffer.transition("closed")
        out = FlushOutcome(
            buffer_stem=buffer.stem,
            state_before=state_before,
            status="trivial",
            note_path=note_path,
            closed=True,
            discarded=True,
        )
        if logger is not None:
            logger.emit(
                "flush-trivial",
                buffer_stem=buffer.stem,
                transcript_id=sidecar.transcript_id,
                turn_count=rb.turn_count,
            )
        _post_flush(
            buffer=buffer,
            outcome=out,
            close=True,
            lore_root=lore_root,
            wiki_root=wiki_root,
            sidecar=sidecar,
            auto_commit=auto_commit,
            logger=logger,
        )
        return out

    # Nothing new since the last chapter — close (if asked) with no chapter.
    if last_idx <= flushed_to:
        return _finish_no_new_turns(
            buffer,
            note_path=note_path,
            close=close,
            state_before=state_before,
            lore_root=lore_root,
            wiki_root=wiki_root,
            rb=rb,
            logger=logger,
        )

    # ---- Compose (LLM, outside the flock) -------------------------------
    turns = _read_buffered_turns(sidecar=sidecar, rb=rb, adapter_lookup=adapter_lookup)
    unflushed = [t for t in turns if t.index > flushed_to]
    compose_result = None
    if unflushed and llm_client is not None and model:
        slice_from = min(t.index for t in unflushed)
        slice_to = max(t.index for t in unflushed)
        compose_result = compose_chapter(
            slice_text=_slice_text(unflushed),
            slice_from_turn=slice_from,
            slice_to_turn=slice_to,
            note_so_far=note_so_far,
            llm_client=llm_client,
            model=model,
            gate=gate,
            logger=logger,
            transcript_id=sidecar.transcript_id,
            turns_by_index={t.index: t.text for t in unflushed},
        )
    else:
        # No readable turns / no client: bound the span to the buffer's
        # reach so a failed marker still records the right turn range.
        slice_from = flushed_to + 1
        slice_to = last_idx

    # ---- Apply the outcome (note + sidecar) under the flock -------------
    with buffer.with_lock():
        outcome = _apply_outcome(
            buffer=buffer,
            note_path=note_path,
            compose_result=compose_result,
            slice_from=slice_from,
            slice_to=slice_to,
            rb=rb,
            close=close,
            state_before=state_before,
            wiki_root=wiki_root,
            lore_root=lore_root,
            logger=logger,
        )

    _post_flush(
        buffer=buffer,
        outcome=outcome,
        close=close,
        lore_root=lore_root,
        wiki_root=wiki_root,
        sidecar=sidecar,
        auto_commit=auto_commit,
        logger=logger,
    )
    return outcome


def _read_note_body(note_path: Path) -> str:
    try:
        return nd.read_note(note_path).body
    except OSError:
        return ""


def _rename_to_topic_slug(buffer: Buffer, note_path: Path, chapter: nd.Chapter) -> Path:
    """Rename a note to its first chapter's topic, once that topic exists.

    The note is created (and first named) at the first heartbeat, well
    before any chapter — so its filename starts as an incidental guess (a
    commit subject, a touched file's basename, or a bare timestamp). Once
    the first chapter composes, its opening lead names the session's
    actual topic; this makes the filename match. A chapter with no usable
    lead text (shouldn't happen for a COMPOSED result, but defensive)
    leaves the filename untouched rather than risk an empty slug.
    """
    lead = _lead_for_rename(chapter)
    if not lead:
        return note_path
    slug = _slug(lead)
    if not slug or slug == "session":
        return note_path
    new_path = _resolve_renamed_path(note_path, slug)
    if new_path == note_path:
        return note_path
    note_path.replace(new_path)
    buffer.patch(stub_path=str(new_path))
    return new_path


@dataclass(frozen=True)
class _ApplyContext:
    """Inputs every outcome handler in one `_apply_outcome` call shares."""

    buffer: Buffer
    note_path: Path
    slice_from: int
    slice_to: int
    rb: ReplayedBuffer
    close: bool
    wiki_root: Path
    lore_root: Path
    logger: RunLogger | None
    sidecar: Sidecar
    facts: Any
    linkage: Any
    store: FlushStore


def _apply_composed(ctx: _ApplyContext, out: FlushOutcome, compose_result, flush_rec) -> bool:
    """Fold a composed chapter into the note.

    Returns False when the append hit the disk and failed — the note must
    not then be closed, and no further writes are attempted.
    """
    try:
        out.chapter_n = nd.append_chapter(
            ctx.note_path,
            compose_result.chapter,
            slice_from_turn=ctx.slice_from,
            slice_to_turn=ctx.slice_to,
            facts=ctx.facts,
            linkage=ctx.linkage,
            wiki_root=ctx.wiki_root,
            title=_topic_title(ctx.sidecar.scope, compose_result.chapter),
        )
    except OSError:
        # A note write that fails on disk is a dead-letter, not a crash:
        # record it and stop writing to a failing filesystem.
        ctx.store.transition(
            flush_rec, FlushState.DEAD_LETTERED, reason=ErrorCode.CHAPTER_APPEND_FAILED
        )
        out.status = "failed"
        return False

    if out.chapter_n == 1:
        # The note was born with a placeholder slug; its first chapter is
        # what finally names it.
        note_path = _rename_to_topic_slug(ctx.buffer, ctx.note_path, compose_result.chapter)
        out.note_path = note_path
        out.wikilink = f"[[{note_path.stem}]]"
    out.status = "composed"
    _clear_after_progress(ctx.buffer, close=ctx.close)
    ctx.store.transition(flush_rec, FlushState.PUBLISHED)
    return True


def _apply_empty(ctx: _ApplyContext, out: FlushOutcome, flush_rec) -> bool:
    """Record a span the composer judged substance-free."""
    out.status = "empty"
    if ctx.close:
        if _flushed_to(ctx.note_path) < 0:
            # The whole session produced nothing of substance: the stub
            # note is removed rather than closed empty.
            with contextlib.suppress(OSError):
                ctx.note_path.unlink()
            out.discarded = True
            out.wikilink = ""
    else:
        # Consume the span (buffer reset, watermark kept) so the same turns
        # are never recomposed; the session stays live.
        _reset_buffer_fresh(ctx.buffer)

    if ctx.logger is not None:
        ctx.logger.emit(
            "flush-empty",
            buffer_stem=ctx.buffer.stem,
            transcript_id=ctx.sidecar.transcript_id,
            discarded=out.discarded,
        )
    # "Nothing of substance" is a completed flush, not a failure.
    ctx.store.transition(flush_rec, FlushState.PUBLISHED)
    return True


def _apply_withheld(ctx: _ApplyContext, out: FlushOutcome, compose_result, flush_rec) -> bool:
    """Quarantine unsafe composed text and leave a marker in its place."""
    verdict = GateResult.withheld(
        compose_result.withheld_category,
        compose_result.withheld_feedback,
    )
    w = pg.apply_withhold(
        ctx.note_path,
        result=verdict,
        composed_text=compose_result.withheld_text,
        slice_from_turn=ctx.slice_from,
        slice_to_turn=ctx.slice_to,
        lore_root=ctx.lore_root,
        wiki_root=ctx.wiki_root,
    )
    out.chapter_n = w.chapter_n
    out.status = "withheld"
    _clear_after_progress(ctx.buffer, close=ctx.close)
    if ctx.logger is not None:
        ctx.logger.emit(
            "flush-withheld",
            buffer_stem=ctx.buffer.stem,
            transcript_id=ctx.sidecar.transcript_id,
            category=verdict.category,
        )
    ctx.store.transition(flush_rec, FlushState.WITHHELD)
    return True


def _mark_failed_span(ctx: _ApplyContext, out: FlushOutcome, reason: str) -> None:
    """Record an uncomposable span as a marker chapter, best-effort."""
    with contextlib.suppress(OSError):
        out.chapter_n = nd.append_marker_chapter(
            ctx.note_path,
            kind=nd.MARKER_FAILED,
            reason=reason,
            slice_from_turn=ctx.slice_from,
            slice_to_turn=ctx.slice_to,
            facts=ctx.facts,
            linkage=ctx.linkage,
            wiki_root=ctx.wiki_root,
        )


def _apply_failed(ctx: _ApplyContext, out: FlushOutcome, flush_rec) -> bool:
    """Handle a failed composition — mark at close, else retry with backoff."""
    if ctx.close:
        # Last chance: no retry left, so the span is recorded as it stands.
        _mark_failed_span(ctx, out, "composition failed at session end")
        out.status = "failed"
        ctx.store.transition(
            flush_rec, FlushState.DEAD_LETTERED, reason=ErrorCode.COMPOSE_FAILED
        )
        return True

    # Bounded retry with backoff. record_failure() re-queues with a scheduled
    # next-eligible-retry (emitting a spine event — never silent) until
    # MAX_ATTEMPTS failures, then dead-letters.
    flush_rec = ctx.store.record_failure(flush_rec, error_code=ErrorCode.COMPOSE_FAILED)
    ctx.buffer.patch(
        flush_attempts=ctx.sidecar.flush_attempts + 1,
        last_error="compose-failed",
        flush_requested=None,
    )

    if FlushState(flush_rec.state) is FlushState.DEAD_LETTERED:
        # Retries exhausted: record the failed span with a marker and reset
        # the buffer — the note stays open, one session one note.
        _mark_failed_span(
            ctx, out, "composition failed after retries; span recorded and buffer reset"
        )
        _reset_buffer_fresh(ctx.buffer)
        out.status = "gave-up"
        if ctx.logger is not None:
            ctx.logger.emit(
                "flush-gave-up",
                buffer_stem=ctx.buffer.stem,
                transcript_id=ctx.sidecar.transcript_id,
                turn_count=ctx.rb.turn_count,
                prompt_chars=ctx.rb.prompt_chars,
            )
        return True

    out.status = "deferred"
    if ctx.logger is not None:
        ctx.logger.emit(
            "flush-deferred",
            buffer_stem=ctx.buffer.stem,
            transcript_id=ctx.sidecar.transcript_id,
            flush_attempts=ctx.sidecar.flush_attempts + 1,
        )
    return True


def _back_off_if_covered(
    buffer: Buffer,
    sidecar: Sidecar,
    note_path: Path,
    slice_to: int,
    out: FlushOutcome,
) -> bool:
    """True when a concurrent flush already published this span.

    The in-place cap-trip can race the reaper: both read the same buffer and
    one publishes while the other is still composing. The loser re-reads the
    note watermark here and drops its own write, or the same turns land as
    two chapters. Only meaningful mid-session — a closing flush has no peer.
    """
    if _flushed_to(note_path) < slice_to:
        return False
    out.status = "skipped"
    out.skipped_reason = "span-already-covered"
    if sidecar.flush_requested is not None:
        buffer.patch(flush_requested=None)
    return True


def _begin_apply(
    *,
    buffer: Buffer,
    note_path: Path,
    sidecar: Sidecar,
    slice_from: int,
    slice_to: int,
    rb: ReplayedBuffer,
    close: bool,
    wiki_root: Path,
    lore_root: Path,
    logger: RunLogger | None,
) -> tuple[_ApplyContext, Any]:
    """Open a flush record and bundle the inputs the outcome handlers share.

    Returns the context plus the flush record, which every branch drives to
    a terminal state.
    """
    facts = facts_from_replay(rb)
    # The logger carries this flush's trace_id (minted at spawn, delivered via
    # env). Stamp it onto the note's linkage and the flush record so run
    # events, the drain event and the note all share one id.
    trace_id = logger.trace_id if logger is not None else None
    linkage = replace(
        linkage_from_replay(
            rb, cwd=sidecar.cwd, wiki_root=wiki_root, handle=sidecar.handle
        ),
        trace_id=trace_id,
    )

    # Track this flush as an explicit, queryable state machine: begin (queued)
    # -> running for this attempt, then a terminal published / withheld /
    # dead-lettered transition. The record is the source of truth for "what is
    # in-flight right now"; every transition also emits a spine event.
    store = FlushStore(lore_root)
    flush_rec = store.begin(buffer.stem, wiki=sidecar.wiki, trace_id=trace_id)
    if FlushState(flush_rec.state) is FlushState.QUEUED:
        flush_rec = store.transition(flush_rec, FlushState.RUNNING)

    ctx = _ApplyContext(
        buffer=buffer,
        note_path=note_path,
        slice_from=slice_from,
        slice_to=slice_to,
        rb=rb,
        close=close,
        wiki_root=wiki_root,
        lore_root=lore_root,
        logger=logger,
        sidecar=sidecar,
        facts=facts,
        linkage=linkage,
        store=store,
    )
    return ctx, flush_rec


def _seal_note(ctx: _ApplyContext, out: FlushOutcome) -> None:
    """Close the note and the buffer at session end."""
    # out.note_path, not ctx.note_path: a first chapter renames the note.
    if not out.discarded and not nd.is_closed(out.note_path):
        nd.close_note(
            out.note_path,
            facts=ctx.facts,
            linkage=ctx.linkage,
            wiki_root=ctx.wiki_root,
        )
    ctx.buffer.transition("closed")
    out.closed = True


def _apply_outcome(
    *,
    buffer: Buffer,
    note_path: Path,
    compose_result,
    slice_from: int,
    slice_to: int,
    rb: ReplayedBuffer,
    close: bool,
    state_before: str,
    wiki_root: Path,
    lore_root: Path,
    logger: RunLogger | None,
) -> FlushOutcome:
    """Fold one compose result into the note and the buffer.

    Dispatches on the compose status; every branch drives the flush record
    to a terminal state, and `close` then seals the note.
    """
    sidecar = buffer.read_sidecar() or Sidecar(transcript_id=buffer.stem)
    out = FlushOutcome(
        buffer_stem=buffer.stem,
        state_before=state_before,
        note_path=note_path,
        wikilink=f"[[{note_path.stem}]]",
    )
    out.attempts = getattr(compose_result, "attempts", 0)

    if not close and _back_off_if_covered(buffer, sidecar, note_path, slice_to, out):
        return out

    ctx, flush_rec = _begin_apply(
        buffer=buffer,
        note_path=note_path,
        sidecar=sidecar,
        slice_from=slice_from,
        slice_to=slice_to,
        rb=rb,
        close=close,
        wiki_root=wiki_root,
        lore_root=lore_root,
        logger=logger,
    )

    status = compose_result.status if compose_result is not None else ComposeStatus.FAILED
    if status is ComposeStatus.COMPOSED:
        sealable = _apply_composed(ctx, out, compose_result, flush_rec)
    elif status is ComposeStatus.EMPTY:
        sealable = _apply_empty(ctx, out, flush_rec)
    elif status is ComposeStatus.WITHHELD:
        sealable = _apply_withheld(ctx, out, compose_result, flush_rec)
    else:
        sealable = _apply_failed(ctx, out, flush_rec)

    if sealable and close:
        _seal_note(ctx, out)
    return out


def _is_trivial(rb: ReplayedBuffer, flushed_to: int) -> bool:
    """True when the whole session is too small to be worth a note.

    Deterministic and conservative: fires only when the note has no
    chapters yet AND the buffer shows a tiny turn count, little text,
    and zero repo activity. Anything bigger goes to the composer, which
    can still answer "nothing of substance" itself.
    """
    if flushed_to >= 0:
        return False
    if rb.turn_count > TRIVIAL_MAX_TURNS or rb.prompt_chars > TRIVIAL_MAX_CHARS:
        return False
    return not (
        rb.files_touched
        or rb.commit_shas
        or rb.activity_commits
        or rb.activity_issues_opened
        or rb.activity_issues_closed
    )


def _clear_after_progress(buffer: Buffer, *, close: bool) -> None:
    """Reset the deferred-failure memory after a chapter / marker landed."""
    if close:
        return
    buffer.patch(flush_attempts=0, last_error=None, flush_requested=None)


def _reset_buffer_fresh(buffer: Buffer) -> None:
    """Reset the buffer's accumulation so one session stays one note.

    The failed span is now recorded as a marker chapter, so the buffer
    starts over: the append log is truncated and counters zeroed (so it
    drops back below the cap), while ``last_seen`` — the transcript
    watermark — is kept so the next heartbeat resumes where it left off.
    Caller holds the flock.
    """
    buffer.log_path.write_text("")
    buffer.patch(
        counters=Counters(),
        flush_attempts=0,
        last_error=None,
        flush_requested=None,
    )


def _finish_no_new_turns(
    buffer: Buffer,
    *,
    note_path: Path,
    close: bool,
    state_before: str,
    lore_root: Path,
    wiki_root: Path,
    rb: ReplayedBuffer,
    logger: RunLogger | None,
) -> FlushOutcome:
    out = FlushOutcome(
        buffer_stem=buffer.stem,
        state_before=state_before,
        note_path=note_path,
        wikilink=f"[[{note_path.stem}]]",
        status="closed-empty" if close else "skipped",
        skipped_reason="" if close else "no-new-turns",
    )
    with buffer.with_lock():
        sc = buffer.read_sidecar()
        if close:
            if not nd.is_closed(note_path):
                linkage = linkage_from_replay(
                    rb,
                    cwd=sc.cwd if sc else "",
                    wiki_root=wiki_root,
                    handle=sc.handle if sc else "",
                )
                nd.close_note(
                    note_path, facts=facts_from_replay(rb), linkage=linkage, wiki_root=wiki_root
                )
            buffer.transition("closed")
            out.closed = True
        else:
            if sc is not None and sc.flush_requested is not None:
                buffer.patch(flush_requested=None)
    if close:
        _archive(buffer, logger=logger)
    return out


def _post_flush(
    *,
    buffer: Buffer,
    outcome: FlushOutcome,
    close: bool,
    lore_root: Path,
    wiki_root: Path,
    sidecar: Sidecar,
    auto_commit: bool,
    logger: RunLogger | None,
) -> None:
    if close:
        _archive(buffer, logger=logger)
    if outcome.note_path is None:
        return
    if outcome.discarded:
        # Stage/commit the stub's deletion so the wiki repo stays clean
        # (a no-op when the stub was never committed). Nothing filed —
        # no drain event.
        if auto_commit:
            with contextlib.suppress(Exception):
                maybe_auto_commit(
                    wiki_root,
                    FiledNote(path=outcome.note_path, wikilink="", was_merge=True),
                    logger,
                    llm_client=None,
                )
        return
    if not outcome.chapter_n:
        return
    if auto_commit:
        # Never block a flush on git.
        with contextlib.suppress(Exception):
            maybe_auto_commit(
                wiki_root,
                FiledNote(path=outcome.note_path, wikilink=outcome.wikilink, was_merge=True),
                logger,
                llm_client=None,
            )
    try:
        from lore_core.drain import DrainStore, resolve_session_id

        sid, _ = resolve_session_id(Path(sidecar.cwd))
        DrainStore(lore_root, sid).emit(
            "note-filed" if close else "note-appended",
            wiki=sidecar.wiki,
            trace_id=logger.trace_id if logger is not None else None,
            wikilink=outcome.wikilink,
            path=str(outcome.note_path),
            transcript_id=sidecar.transcript_id,
        )
    except Exception:  # noqa: BLE001 - drain emit is best-effort
        pass


def _archive(buffer: Buffer, *, logger: RunLogger | None) -> None:
    try:
        buffer.close()
    except OSError:
        pass
    except BufferTransitionError as exc:
        if logger is not None:
            logger.emit(
                "warning",
                reason="done-archive-collision",
                stem=buffer.stem,
                detail=str(exc),
            )


# ---------------------------------------------------------------------------
# Startup sweep — singleton close of dead sessions
# ---------------------------------------------------------------------------


@dataclass
class SweepReport:
    scanned: int = 0
    swept: int = 0
    alive_skipped: int = 0
    uncertain_skipped: int = 0
    stale_closed: int = 0  # dead + too old → closed with a marker, no LLM call
    deferred: int = 0  # recent dead buffers left for the next sweep (budget hit)
    discarded: int = 0  # trivial / all-empty sessions whose stub was removed
    contended: bool = False
    swept_stems: list[str] = field(default_factory=list)


def startup_sweep(
    lore_root: Path,
    *,
    llm_client: Any = None,
    adapter_lookup=None,
    logger: RunLogger | None = None,
    host: str | None = None,
    lock_timeout: float = 0.0,
    stale_days: int = SWEEP_STALE_DAYS,
    max_compose: int = SWEEP_MAX_COMPOSE,
) -> SweepReport:
    """Sweep dead-session buffers under the global singleton lock.

    lore is a singleton at startup: the sweep holds the global
    ``curator.lock`` so concurrent session starts race safely — the
    loser sees the lock contended and returns without touching anything
    (``report.contended``). The winner closes dead sessions' notes,
    bounded so a deep backlog never stalls startup (see
    :func:`sweep_dead_sessions`).
    """
    try:
        with curator_lock(lore_root, timeout=lock_timeout):
            return sweep_dead_sessions(
                lore_root,
                llm_client=llm_client,
                adapter_lookup=adapter_lookup,
                logger=logger,
                host=host,
                stale_days=stale_days,
                max_compose=max_compose,
            )
    except LockContendedError:
        return SweepReport(contended=True)


def _age_days(local_date_str: str) -> int | None:
    """Whole days between ``local_date_str`` (YYYY-MM-DD) and today (UTC).

    Returns ``None`` when the date is missing or unparseable, which the
    caller treats as "recent" (compose) rather than stale — better to
    spend one compose than to silently drop a session on a bad date.
    """
    if not local_date_str:
        return None
    try:
        d = datetime.strptime(local_date_str, "%Y-%m-%d").date()
    except ValueError:
        return None
    return (datetime.now(UTC).date() - d).days


def sweep_dead_sessions(
    lore_root: Path,
    *,
    llm_client: Any = None,
    adapter_lookup=None,
    logger: RunLogger | None = None,
    host: str | None = None,
    stale_days: int = SWEEP_STALE_DAYS,
    max_compose: int = SWEEP_MAX_COMPOSE,
) -> SweepReport:
    """Close the notes of dead-owner buffers, bounded so startup never stalls.

    A naive "compose every dead buffer" sweep chokes on a deep backlog —
    each compose is an LLM call, so months of abandoned buffers can hang
    SessionStart for minutes. Instead:

    * dead buffers are handled **newest-first**, so a live session's own
      recent work drains before the per-run budget is spent;
    * a dead buffer **older than ``stale_days``** is closed with a marker
      and **no LLM call** — its transcript is likely gone and a stale
      compose is wasteful; this clears the backlog cheaply;
    * at most ``max_compose`` recent dead buffers are composed per sweep;
      the rest are left for the next sweep (they drain over successive
      starts) so one startup is always bounded.

    Live owners are left alone; uncertain liveness (no ``/proc`` / macOS /
    network fs) is left to the reaper's staleness path rather than swept.
    """
    from lore_curator.reaper import is_owner_alive

    report = SweepReport()
    dead: list[tuple[str, Any, Any]] = []
    for buf in iter_all(lore_root):
        sidecar = buf.read_sidecar()
        if sidecar is None or sidecar.state == "closed":
            continue
        report.scanned += 1
        verdict = is_owner_alive(sidecar, host=host)
        if verdict is True:
            report.alive_skipped += 1
            continue
        if verdict is None:
            report.uncertain_skipped += 1
            continue
        dead.append((sidecar.local_date or "", buf, sidecar))

    # Newest local_date first; recent sessions win the compose budget.
    dead.sort(key=lambda t: t[0], reverse=True)

    composed = 0
    for local_date_str, buf, sidecar in dead:
        wiki_dir = lore_root / "wiki" / sidecar.wiki
        age = _age_days(local_date_str)
        if age is not None and age > stale_days:
            # Too old to compose: close with a marker, no model call.
            # A trivial stale session discards its stub instead.
            oc = synth_and_close(
                buf.sidecar_path,
                lore_root=lore_root,
                wiki_root=wiki_dir,
                llm_client=None,
                model=None,
                adapter_lookup=adapter_lookup,
                logger=logger,
            )
            if oc.discarded:
                report.discarded += 1
            else:
                report.stale_closed += 1
            report.swept_stems.append(buf.stem)
            if logger is not None:
                logger.emit(
                    "sweep-stale-closed",
                    buffer_stem=buf.stem,
                    transcript_id=sidecar.transcript_id,
                    age_days=age,
                )
            continue
        if composed >= max_compose:
            report.deferred += 1
            continue
        model = _resolve_model(wiki_dir)
        oc = synth_and_close(
            buf.sidecar_path,
            lore_root=lore_root,
            wiki_root=wiki_dir,
            llm_client=llm_client,
            model=model,
            adapter_lookup=adapter_lookup,
            logger=logger,
        )
        if oc.status != "trivial":
            # Everything past the trivial gate spent an LLM call —
            # including an "empty" answer — so it consumes the budget.
            composed += 1
        if oc.discarded:
            report.discarded += 1
        else:
            report.swept += 1
        report.swept_stems.append(buf.stem)
        if logger is not None:
            logger.emit("sweep-closed", buffer_stem=buf.stem, transcript_id=sidecar.transcript_id)
    return report


def _resolve_model(wiki_root: Path) -> str | None:
    try:
        from lore_core.wiki_config import load_wiki_config

        cfg = load_wiki_config(wiki_root)
        tier = cfg.curator.synthesis_model_tier
        return {
            "simple": cfg.models.simple,
            "middle": cfg.models.middle,
            "high": cfg.models.high,
        }.get(tier, cfg.models.middle)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Detached flush spawn (fire-and-forget subprocess)
# ---------------------------------------------------------------------------


def _emit_spawn_failed(buffer_path: Path, lore_root: Path, exc: Exception, trace_id: str) -> None:
    """A detached-flush spawn that fails is no longer swallowed silently."""
    SpineWriter(lore_root).emit(
        source="curator",
        event="flush-spawn-failed",
        level="error",
        trace_id=trace_id,
        error_code=ErrorCode.SPAWN_FAILED,
        data={"buffer": buffer_path.name, "error": str(exc)},
    )


def spawn_detached_flush(
    buffer_path: Path, *, lore_root: Path, trace_id: str | None = None
) -> str | None:
    """Fire-and-forget ``lore curator flush <buffer-path> --config-from-buffer``.

    Mints a ``trace_id`` (unless the caller supplies one) and delivers it to
    the detached curator via ``LORE_TRACE_ID`` — the one place the id crosses
    the process boundary. Returns the trace_id on a successful Popen, or
    ``None`` on a skipped/failed spawn. Never blocks the caller. A per-buffer
    spawn-lock under ``<stem>.spawn.lock`` prevents a double-spawn when a
    reaper races cap-trip; a held lock skips rather than queueing.
    """
    import os
    import subprocess
    import sys

    from lore_core.lockfile import flocked

    spawn_lock = buffer_path.with_suffix(".spawn.lock")
    # Minted before the lock so even a flock failure emits a traceable event.
    trace_id = trace_id or new_trace_id()
    try:
        with flocked(spawn_lock, blocking=False) as held:
            if not held:
                return None
            cmd = [
                sys.executable,
                "-m",
                "lore_cli",
                "curator",
                "flush",
                "--config-from-buffer",
                str(buffer_path),
            ]
            env = os.environ.copy()
            env["LORE_ROOT"] = str(lore_root)
            env["LORE_CURATOR_MODE"] = "1"
            env["LORE_TRACE_ID"] = trace_id
            try:
                subprocess.Popen(
                    cmd,
                    cwd=str(lore_root),
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    env=env,
                )
                return trace_id
            except (OSError, subprocess.SubprocessError) as exc:
                _emit_spawn_failed(buffer_path, lore_root, exc, trace_id)
                return None
    except OSError as exc:
        _emit_spawn_failed(buffer_path, lore_root, exc, trace_id)
        return None
