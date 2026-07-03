"""Heartbeat append for the buffer-and-flush curator.

This is the curator-A heartbeat path; the legacy per-chunk
classify+synthesise+merge path was deleted in PR 6b of the
streamlining track (issue #80). Each heartbeat:

1. Resolves the ``(transcript_id, local_date)`` buffer.
2. Runs the deterministic activity collectors (``_collect_activity``).
3. Appends one JSONL event (slice pointers + deltas + Activity bullets).
4. Patches the sidecar (counters, last_heartbeat, owner).
5. Reports back what the caller needs to write the stub note and decide
   whether to flip the buffer to ``ready`` (cap-trip in Step 8).

This module owns the *append + sidecar* responsibility. The stub-note
write lives in ``stub_note.py``; the flush worker lives in ``synthesis.py``
(Step 4). The caller wires them together.

No LLM is invoked here. Every byte that lands in the buffer is
deterministic — recoverable by replay, idempotent under retries, and
recomputable from the transcript slice if the JSONL is ever lost.
"""
from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lore_core.types import Turn
from lore_core.wiki_config import WikiConfig
from lore_curator.buffer_store import (
    Buffer,
    Counters,
    FlushRequest,
    LastSeen,
    OwnerInfo,
    Sidecar,
    _now_iso,
)
from lore_curator.session_activity import (
    _collect_activity,
    _files_modified_from_turns,
    _files_read_from_turns,
    _files_touched_from_turns,
)

if TYPE_CHECKING:
    from lore_core.run_log import RunLogger


__all__ = ["AppendOutcome", "append_chunk"]


@dataclass
class AppendOutcome:
    """What ``append_chunk`` reports back to the caller.

    ``activity`` carries the same dict shape ``_collect_activity`` returns
    (commits / issues_opened / issues_closed / projects / commit_shas)
    so the caller can render the stub body without a second collector
    pass.
    """

    buffer: Buffer
    is_new_buffer: bool
    activity: dict[str, Any]
    files_touched: list[str]
    last_hash: str
    last_index: int
    cap_tripped: bool = False
    flush_requested: bool = False  # True if sidecar already had a flush_requested marker on entry
    accumulators_unchanged: bool = False  # True when nothing new beyond last_heartbeat
    sidecar_after: Sidecar | None = None
    skipped_no_op: bool = False
    new_files_touched: list[str] = field(default_factory=list)
    new_files_modified: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)
    new_files_read: list[str] = field(default_factory=list)
    new_projects: list[str] = field(default_factory=list)
    new_commit_shas: list[str] = field(default_factory=list)


def _owner_start_ticks(pid: int) -> float:
    """Return ``/proc/<pid>/stat`` field 22 (clock ticks since boot) on Linux.

    Returns 0.0 on platforms / containers where ``/proc/<pid>/stat`` isn't
    available — the reaper's start_ts comparison short-circuits in that
    case (Step 7 macOS fallback).
    """
    try:
        with open(f"/proc/{pid}/stat", "r") as fh:
            content = fh.read()
    except (FileNotFoundError, PermissionError, OSError):
        return 0.0
    # Field 2 (comm) may contain spaces and parens; everything after the
    # last ``)`` is space-separated, so split on it.
    rparen = content.rfind(")")
    if rparen < 0:
        return 0.0
    fields = content[rparen + 1 :].split()
    # Field 22 is index 19 in the post-comm slice (fields 3..N -> 0..N-3).
    if len(fields) < 20:
        return 0.0
    try:
        return float(fields[19])
    except ValueError:
        return 0.0


def _prompt_chars(turns: list[Turn]) -> int:
    """Cheap character budget proxy.

    Counts ``Turn.text`` for every role plus ``tool_result.output``. The
    cap (``synthesis_buffer_cap_chars``) bounds what Phase 2's LLM might
    re-read at flush time, so we err on the side of including everything
    the slice actually carries.
    """
    total = 0
    for t in turns:
        if t.text:
            total += len(t.text)
        if t.tool_result is not None and isinstance(t.tool_result.output, str):
            total += len(t.tool_result.output)
    return total


def _build_initial_sidecar(
    *,
    transcript_id: str,
    local_date: str,
    integration: str,
    wiki: str,
    scope: str,
    cwd: Path,
    handle_label: str,
    owner: OwnerInfo,
) -> Sidecar:
    return Sidecar(
        transcript_id=transcript_id,
        local_date=local_date,
        integration=integration,
        wiki=wiki,
        scope=scope,
        cwd=str(cwd),
        handle=handle_label,
        owner=owner,
        state="accumulating",
        counters=Counters(),
        last_seen=LastSeen(),
    )


def _build_owner(*, run_id: str, claude_session_id: str) -> OwnerInfo:
    pid = os.getpid()
    return OwnerInfo(
        pid=pid,
        start_ts=_owner_start_ticks(pid),
        run_id=run_id,
        host=socket.gethostname(),
        claude_session_id=claude_session_id,
    )


def append_chunk(
    *,
    lore_root: Path,
    chunk_turns: list[Turn],
    local_date: str,
    transcript_id: str,
    integration: str,
    wiki: str,
    scope: str,
    cwd: Path,
    wiki_root: Path,
    cfg: WikiConfig,
    handle_label: str = "",
    owner_run_id: str = "",
    owner_claude_session_id: str = "",
    logger: "RunLogger | None" = None,
) -> AppendOutcome:
    """Append one heartbeat's worth of chunk data to the buffer.

    Returns an :class:`AppendOutcome` with the activity payload (so the
    caller can render the stub note without re-running collectors), the
    updated sidecar, and flags the caller uses to decide whether to flip
    the buffer to ``ready`` (cap-trip — Step 8) or skip stub-note rewrite
    (no-op heartbeat).

    Idempotency: passing the same chunk twice yields a second JSONL event
    line whose deltas dedup against the replayed accumulator — counters
    advance (turn_count is summed, not folded), but the
    de-duped lists (files_touched / plans / projects / commit_shas) stay
    stable. Test ``test_buffer_append_idempotent`` exercises the contract.
    """
    if not chunk_turns:
        # No-op heartbeat: caller had nothing new for this transcript /
        # date. Open the buffer for status reads but don't write.
        buffer = Buffer.open(
            lore_root,
            transcript_id=transcript_id,
            local_date=local_date,
        )
        return AppendOutcome(
            buffer=buffer,
            is_new_buffer=False,
            activity={},
            files_touched=[],
            files_modified=[],
            files_read=[],
            last_hash="",
            last_index=-1,
            skipped_no_op=True,
            sidecar_after=buffer.read_sidecar(),
        )

    buffer = Buffer.open(
        lore_root,
        transcript_id=transcript_id,
        local_date=local_date,
    )

    files_touched = _files_touched_from_turns(chunk_turns)
    files_modified = _files_modified_from_turns(chunk_turns)
    files_read = _files_read_from_turns(chunk_turns)

    activity = _collect_activity(
        cwd=cwd,
        wiki_root=wiki_root,
        turns=chunk_turns,
        files_touched=files_touched,
        logger=logger,
    )

    from_hash = chunk_turns[0].content_hash()
    to_hash = chunk_turns[-1].content_hash()
    from_index = chunk_turns[0].index
    to_index = chunk_turns[-1].index

    turn_count_delta = len(chunk_turns)
    prompt_chars_delta = _prompt_chars(chunk_turns)

    event: dict[str, Any] = {
        "type": "append",
        "slice": {
            "from_hash": from_hash,
            "to_hash": to_hash,
            "from_index": from_index,
            "to_index": to_index,
        },
        "turn_count_delta": turn_count_delta,
        "prompt_chars_delta": prompt_chars_delta,
        "files_touched": files_touched,
        "files_modified": files_modified,
        "files_read": files_read,
        "projects": list(activity.get("projects") or []),
        "commit_shas": list(activity.get("commit_shas") or []),
        "activity_commits": list(activity.get("commits") or []),
        "activity_issues_opened": list(activity.get("issues_opened") or []),
        "activity_issues_closed": list(activity.get("issues_closed") or []),
    }

    cap_tripped = False
    flush_requested = False
    accumulators_unchanged = False
    is_new = False
    sidecar_after: Sidecar | None = None
    new_files: list[str] = []
    new_projects: list[str] = []
    new_commit_shas: list[str] = []

    with buffer.with_lock():
        existing = buffer.read_sidecar()

        if existing is None:
            owner = _build_owner(
                run_id=owner_run_id,
                claude_session_id=owner_claude_session_id,
            )
            sidecar = _build_initial_sidecar(
                transcript_id=transcript_id,
                local_date=local_date,
                integration=integration,
                wiki=wiki,
                scope=scope,
                cwd=cwd,
                handle_label=handle_label,
                owner=owner,
            )
            buffer.init_sidecar(sidecar)
            is_new = True
            existing = buffer.read_sidecar()
            if logger is not None:
                logger.emit(
                    "buffer-opened",
                    transcript_id=transcript_id,
                    local_date=local_date,
                    stem=buffer.stem,
                )

        # State guard: if a flush worker has already taken ownership
        # (state in {flushing, closed}), the heartbeat must NOT mutate
        # the buffer. The caller treats this as a no-op heartbeat.
        if existing.state in ("flushing", "closed"):
            return AppendOutcome(
                buffer=buffer,
                is_new_buffer=is_new,
                activity=activity,
                files_touched=files_touched,
                last_hash=to_hash,
                last_index=to_index,
                flush_requested=existing.flush_requested is not None,
                skipped_no_op=True,
                sidecar_after=existing,
            )

        flush_requested = existing.flush_requested is not None

        # Compute "what's new" *before* the append so the caller can
        # decide whether the stub note needs a body rewrite. We replay
        # the pre-append log and compare deduped-extend against it.
        pre = buffer.replay()
        pre_files = set(pre.files_touched)
        pre_files_modified = set(pre.files_modified)
        pre_files_read = set(pre.files_read)
        pre_projects = set(pre.projects)
        pre_shas = set(pre.commit_shas)
        new_files = [f for f in files_touched if f not in pre_files]
        new_files_modified = [f for f in files_modified if f not in pre_files_modified]
        new_files_read = [f for f in files_read if f not in pre_files_read]
        new_projects = [p for p in (activity.get("projects") or []) if p not in pre_projects]
        new_commit_shas = [
            s for s in (activity.get("commit_shas") or []) if s not in pre_shas
        ]
        # Activity bullets follow the same accumulator semantics — when
        # files/projects/commits are all duplicates and no new Activity
        # content appeared, the heartbeat hasn't changed the stub's
        # body. ``last_heartbeat`` still moves; that's a sidecar patch,
        # not a body rewrite.
        pre_activity_commits = set(pre.activity_commits)
        pre_activity_issues_opened = set(pre.activity_issues_opened)
        pre_activity_issues_closed = set(pre.activity_issues_closed)
        new_activity_total = (
            sum(1 for x in (activity.get("commits") or []) if x not in pre_activity_commits)
            + sum(1 for x in (activity.get("issues_opened") or []) if x not in pre_activity_issues_opened)
            + sum(1 for x in (activity.get("issues_closed") or []) if x not in pre_activity_issues_closed)
        )
        accumulators_unchanged = (
            not new_files
            and not new_projects
            and not new_commit_shas
            and new_activity_total == 0
        )

        buffer.append_event(event)

        # Re-replay so counters reflect the post-append state.
        post = buffer.replay()
        new_counters = Counters(
            turn_count=post.turn_count,
            prompt_chars=post.prompt_chars,
            files_touched_count=len(post.files_touched),
            files_modified_count=len(post.files_modified),
            files_read_count=len(post.files_read),
        )
        last_seen = LastSeen(content_hash=to_hash, index_hint=to_index)

        # Owner refresh on every heartbeat — same pid is fine; bumps
        # run_id when the curator re-spawned (the prior owner's run is
        # over so the buffer now belongs to this one).
        owner = _build_owner(
            run_id=owner_run_id,
            claude_session_id=owner_claude_session_id,
        )

        # State stays at accumulating; only counters / heartbeat / owner
        # / last_seen advance. The cap-trip transition (-> ready) is
        # gated below.
        sidecar_after = buffer.transition(
            "accumulating",
            owner=owner,
            counters=new_counters,
            last_seen=last_seen,
            last_heartbeat=_now_iso(),
            last_appended_at=_now_iso(),
        )

        if logger is not None:
            logger.emit(
                "buffer-appended",
                transcript_id=transcript_id,
                local_date=local_date,
                stem=buffer.stem,
                turn_count=new_counters.turn_count,
                prompt_chars=new_counters.prompt_chars,
                files_touched_count=new_counters.files_touched_count,
            )

        # Cap check. A cap-trip is a flush trigger, not a session
        # boundary: the note is append-only until close, so the buffer
        # stays ``accumulating`` and the flush is requested in-place
        # (append a chapter, keep folding into the same buffer). One
        # session yields one note — no splitting.
        if (
            new_counters.turn_count >= cfg.curator.synthesis_buffer_cap_turns
            or new_counters.prompt_chars >= cfg.curator.synthesis_buffer_cap_chars
        ):
            buffer.append_event({"type": "cap-tripped"})
            sidecar_after = buffer.patch(
                flush_requested=FlushRequest(
                    trigger="cap-trip",
                    requested_at=_now_iso(),
                    by_pid=os.getpid(),
                    mode="in_place",
                ),
            )
            cap_tripped = True
            if logger is not None:
                logger.emit(
                    "buffer-cap-tripped",
                    transcript_id=transcript_id,
                    local_date=local_date,
                    stem=buffer.stem,
                    turn_count=new_counters.turn_count,
                    prompt_chars=new_counters.prompt_chars,
                )

    return AppendOutcome(
        buffer=buffer,
        is_new_buffer=is_new,
        activity=activity,
        files_touched=files_touched,
        files_modified=files_modified,
        files_read=files_read,
        last_hash=to_hash,
        last_index=to_index,
        cap_tripped=cap_tripped,
        flush_requested=flush_requested,
        accumulators_unchanged=accumulators_unchanged,
        sidecar_after=sidecar_after,
        new_files_touched=new_files,
        new_files_modified=new_files_modified,
        new_files_read=new_files_read,
        new_projects=new_projects,
        new_commit_shas=new_commit_shas,
    )
