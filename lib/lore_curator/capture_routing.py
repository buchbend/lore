"""Capture-side routing: transcript registration, flush requests, spawn gate.

The decision layer between a hook firing and the curator doing work. Hooks
supply the trigger and the adapter; everything here is about *which* buffers
and transcripts that trigger should route, and whether the accumulated work
has earned a curator spawn.

Host-agnostic — no Claude-Code specifics, no CLI imports. The adapter is
passed in by the caller so this module never has to know what an integration
is.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lore_core.ledger import TranscriptLedger, TranscriptLedgerEntry
from lore_core.spine import ErrorCode, emit_hook_event

if TYPE_CHECKING:
    from lore_core.types import Scope
    from lore_core.wiki_config import WikiConfig


_HANDOVER_RECENT_S = 3600   # 60 min — "the same Claude project recently"
_HANDOVER_POLL_INTERVAL_S = 0.1


def now_utc() -> datetime:
    """Return datetime.now(UTC). Isolated as a seam so tests can pin time."""
    return datetime.now(UTC)


def register_pending_transcripts(
    lore_root: Path,
    cwd: Path,
    *,
    adapter: Any,
    transcript: Path | None = None,
) -> None:
    """List transcripts for ``cwd`` and upsert into the ledger.

    Shared by the capture hook (SessionStart/End/PreCompact) and
    ``user-prompt-submit`` (mid-session). Closes the SessionStart-vs-
    transcript-creation race for sessions whose transcript file did not
    exist when SessionStart sampled the directory: any subsequent
    UserPromptSubmit picks the missing entry up. mtime updates also
    propagate so ``pending()`` sees work growing across a long session
    and the heartbeat spawn-gate can fire mid-session for semantic
    capture rather than waiting for SessionEnd.

    Attach-time watermark: transcripts older than the attachment's
    ``attached_at`` are pre-stamped as already seen so only future
    sessions are pending. Use ``lore backfill`` to opt in to history.

    Bulk-upserted in one ledger serialisation regardless of how many
    handles change — keeps the call well within the hook budget.
    """
    from lore_core.state.attachments import AttachmentsFile

    if transcript is not None:
        handles = [h for h in adapter.list_transcripts(cwd) if h.path == transcript]
    else:
        handles = adapter.list_transcripts(cwd)

    if not handles:
        return

    tledger = TranscriptLedger(lore_root)
    af = AttachmentsFile(lore_root)
    af.load()
    attachment = af.longest_prefix_match(cwd)

    to_write: list[TranscriptLedgerEntry] = []
    for h in handles:
        entry = tledger.get(h.integration, h.id)
        if entry is None:
            is_historical = (
                attachment is not None
                and h.mtime < attachment.attached_at
            )
            to_write.append(
                TranscriptLedgerEntry(
                    integration=h.integration,
                    transcript_id=h.id,
                    path=h.path,
                    directory=h.cwd,
                    digested_hash=None,
                    digested_index_hint=None,
                    synthesised_hash=None,
                    last_mtime=h.mtime,
                    curator_a_run=attachment.attached_at if is_historical else None,
                    noteworthy=None,
                    session_note=None,
                )
            )
        elif entry.last_mtime != h.mtime:
            entry.last_mtime = h.mtime
            to_write.append(entry)
    if to_write:
        tledger.bulk_upsert(to_write)


def poll_buffer_handover(
    lore_root: Path,
    cwd: Path,
    *,
    timeout_s: float = 5.0,
) -> list[str]:
    """Wait briefly for in-flight buffer flushes that match this cwd to close.

    Returns a list of context lines to inject into the SessionStart
    output. Each line is either ``> Picked up [[<slug>]]`` for a freshly
    closed buffer or ``> Previous session being synthesised...`` for the
    timeout case.
    """
    import time as _t

    from lore_curator.buffer_store import iter_all

    cwd_str = str(cwd)
    deadline = _t.monotonic() + timeout_s

    candidates: dict[str, dict[str, Any]] = {}
    now = datetime.now(UTC)
    for buf in iter_all(lore_root):
        sidecar = buf.read_sidecar()
        if sidecar is None or sidecar.flush_requested is None:
            continue
        if sidecar.cwd == cwd_str:
            candidates[buf.stem] = {"buf": buf, "matched_via": "cwd"}
            continue
        last = sidecar.last_appended_at
        try:
            ts = datetime.fromisoformat(last.replace("Z", "+00:00")) if last else None
        except ValueError:
            ts = None
        if ts is not None and (now - ts).total_seconds() < _HANDOVER_RECENT_S:
            candidates[buf.stem] = {"buf": buf, "matched_via": "recent"}

    if not candidates:
        return []

    closed_wikilinks: list[str] = []
    pending_stems: set[str] = set(candidates.keys())

    while _t.monotonic() < deadline and pending_stems:
        for stem in list(pending_stems):
            entry = candidates[stem]
            buf = entry["buf"]
            sidecar = buf.read_sidecar()
            # Buffer relocated to _done/ -> read the moved sidecar.
            if sidecar is None:
                done_sidecar = lore_root / ".lore" / "buffers" / "_done" / f"{stem}.state.json"
                if done_sidecar.exists():
                    try:
                        raw = json.loads(done_sidecar.read_text())
                        from lore_curator.buffer_store import Sidecar as _SC
                        sidecar = _SC.from_dict(raw)
                    except (OSError, json.JSONDecodeError, ValueError):
                        sidecar = None
            if sidecar is None:
                pending_stems.discard(stem)
                continue
            if sidecar.state == "closed":
                if sidecar.stub_path:
                    stub = Path(sidecar.stub_path)
                    closed_wikilinks.append(f"[[{stub.stem}]]")
                pending_stems.discard(stem)
        if pending_stems:
            _t.sleep(_HANDOVER_POLL_INTERVAL_S)

    lines: list[str] = []
    for wikilink in closed_wikilinks:
        lines.append(f"> Picked up {wikilink} from a prior session.")
    if pending_stems:
        with contextlib.suppress(Exception):
            emit_hook_event(
                lore_root,
                event="session-start",
                outcome="flush-handover-timeout",
                error_code=ErrorCode.FLUSH_HANDOVER_TIMEOUT,
                cwd=str(cwd),
                pending=sorted(pending_stems),
            )
        lines.append(
            "> Previous session note still being synthesised — it will appear "
            "in a subsequent heartbeat."
        )
    return lines


def request_flush_for_my_buffers(
    lore_root: Path,
    *,
    trigger: str,
    max_scan: int = 20,
) -> int:
    """Stamp ``flush_requested`` on every live buffer owned by this PID.

    Walks ``.lore/buffers/*.state.json`` (sidecar-only, bounded by
    ``max_scan``); for each match, takes the per-buffer flock and stamps
    a ``FlushRequest`` payload. Returns the count of buffers stamped.

    Mode routing:

    - ``trigger in {"session-end", "pre-compact"}`` → ``mode="in_place"``.
      The buffer stays in ``accumulating`` and the worker runs
      :func:`synth_in_place`, which refreshes the on-disk note without
      closing or archiving. This is what keeps a long-running
      conversation as one note per ``(transcript_id, local_date)``
      across infrastructure boundaries.
    - Other triggers (``cap-trip``, ``reaper``) → ``mode="close"`` plus
      the legacy ``accumulating -> ready`` CAS so the worker runs
      :func:`synth_and_close` and archives to ``_done/``.

    Buffers already in ``ready`` / ``flushing`` / ``closed`` states are
    untouched — another path (cap-trip, prior session-end) already
    routed them.
    """
    from lore_curator.buffer_store import (
        BufferTransitionError,
        FlushRequest,
        iter_for_pid,
    )

    in_place_triggers = {"session-end", "pre-compact"}
    mode = "in_place" if trigger in in_place_triggers else "close"

    stamped = 0
    pid = os.getpid()
    now_iso = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    scanned = 0
    for buf in iter_for_pid(lore_root, pid):
        scanned += 1
        if scanned > max_scan:
            break
        try:
            with buf.with_lock(blocking=False) as held:
                if not held:
                    continue
                sidecar = buf.read_sidecar()
                if sidecar is None or sidecar.state in ("flushing", "closed"):
                    continue
                # Skip if a request is already stamped — another path
                # already routed this buffer.
                if sidecar.flush_requested is not None:
                    continue
                req = FlushRequest(
                    trigger=trigger,
                    requested_at=now_iso,
                    by_pid=pid,
                    mode=mode,
                )
                if mode == "in_place":
                    # Stay in ``accumulating`` — the buffer remains live
                    # and may absorb more chunks before the next close.
                    if sidecar.state != "accumulating":
                        continue
                    buf.patch(flush_requested=req)
                else:  # close
                    if sidecar.state == "accumulating":
                        try:
                            buf.transition("ready", flush_requested=req)
                        except BufferTransitionError:
                            continue
                    else:  # "ready" without flush_requested
                        buf.patch(flush_requested=req)
                stamped += 1
        except OSError:
            continue
    return stamped


def wiki_should_spawn(
    entries: list[TranscriptLedgerEntry],
    wiki_cfg: WikiConfig,
    *,
    now: datetime,
) -> tuple[bool, str]:
    """Decide whether Curator A should spawn for a single wiki's pending bucket.

    Pure-functional — no I/O. Reads only fields cached on the ledger entry
    (``total_turns`` is stamped by ``transcript_sync``). Safe to call from
    every UserPromptSubmit heartbeat without measurable cost.

    Returns ``(spawn, reason)``. The reason is a short string emitted into
    hook telemetry so we can debug "why did/didn't this spawn?" without
    re-deriving the gate inputs.

    OR-gate:
      * sum(total_turns − digested_index_hint) ≥ threshold_pending_turns
      * (now − min(last_mtime)) ≥ max_pending_age_s   — age fallback so old
        work below the turns threshold still files within bounded latency.
    """
    if not entries:
        return False, "empty"
    new_turns = sum(
        max(0, e.total_turns - (e.digested_index_hint or 0))
        for e in entries
    )
    if new_turns >= wiki_cfg.curator.threshold_pending_turns:
        return (
            True,
            f"turns:{new_turns}>={wiki_cfg.curator.threshold_pending_turns}",
        )
    oldest_mtime = min(e.last_mtime for e in entries)
    age_s = int((now - oldest_mtime).total_seconds())
    if age_s >= wiki_cfg.curator.max_pending_age_s:
        return True, f"age:{age_s}s>={wiki_cfg.curator.max_pending_age_s}s"
    return False, f"under(turns={new_turns},age={age_s}s)"


@dataclass(frozen=True)
class CaptureRouting:
    """What :func:`route_capture` did, for the caller's telemetry."""

    outcome: str
    pending_after: int
    pending_by_wiki: dict[str, int]
    spawn_reasons: dict[str, str]
    #: Set when the flush-request pass failed. Non-fatal by contract — the
    #: caller reports it as a warning and the capture still completes.
    flush_error: Exception | None = None


def route_capture(
    lore_root: Path,
    cwd: Path,
    scope: Scope,
    *,
    event: str,
    adapter: Any,
    transcript: Path | None,
    spawn_curator_a: Callable[..., bool],
    progress: dict[str, Any] | None = None,
) -> CaptureRouting:
    """Register transcripts, route flushes, and spawn Curator A if the gate trips.

    The decision core of the capture hook. ``spawn_curator_a`` is injected —
    launching subprocesses is the CLI layer's job and this package must not
    import it.

    ``progress`` is filled in as soon as each value is known, so a caller
    whose telemetry runs in an ``except`` block can still report the pending
    counts computed before the failure.

    Force-spawn at session-end / pre-compact regardless of the gate, so no
    in-flight work is stranded across a session boundary (handover guarantee).
    """
    from lore_core.wiki_config import load_wiki_config

    if progress is None:
        progress = {}

    tledger = TranscriptLedger(lore_root)
    register_pending_transcripts(lore_root, cwd, adapter=adapter, transcript=transcript)

    # Buffer-and-flush: at session-end / pre-compact, stamp ``flush_requested``
    # on this session's live buffers so the detached curator-A spawn (or a
    # manual ``lore curator flush``) routes them to synthesis. Bounded sidecar
    # reads keep the hook inside its sub-100ms contract.
    flush_error: Exception | None = None
    if event in ("session-end", "pre-compact"):
        try:
            request_flush_for_my_buffers(lore_root, trigger=event, max_scan=20)
        except Exception as exc:  # noqa: BLE001 - hook must never fail on this
            flush_error = exc

    pending_after = len(tledger.pending())
    buckets = tledger.pending_by_wiki()
    # Counts-dict for telemetry (includes __orphan__/__unattached__ buckets).
    pending_by_wiki = {k: len(v) for k, v in buckets.items()}
    progress["pending_after"] = pending_after
    progress["pending_by_wiki"] = pending_by_wiki

    cfg = load_wiki_config(lore_root / "wiki" / scope.wiki)

    now = now_utc()
    crossed: list[str] = []
    spawn_reasons: dict[str, str] = {}
    for wiki_name, entries in buckets.items():
        if wiki_name.startswith("__"):
            continue
        if len(entries) == 0:
            continue
        wiki_cfg = load_wiki_config(lore_root / "wiki" / wiki_name)
        should, reason = wiki_should_spawn(entries, wiki_cfg, now=now)
        spawn_reasons[wiki_name] = reason
        if should:
            crossed.append(wiki_name)

    force_eos = event in ("session-end", "pre-compact") and pending_after > 0

    if crossed or force_eos:
        spawned = spawn_curator_a(
            lore_root, cooldown_s=cfg.curator.curator_a_cooldown_s
        )
        if spawned:
            outcome = (
                "spawned-curator-eos"
                if (force_eos and not crossed)
                else "spawned-curator"
            )
        else:
            outcome = "spawn-cooldown"
    elif pending_after > 0:
        outcome = "below-threshold"
    else:
        outcome = "no-new-turns"

    return CaptureRouting(
        outcome=outcome,
        pending_after=pending_after,
        pending_by_wiki=pending_by_wiki,
        spawn_reasons=spawn_reasons,
        flush_error=flush_error,
    )
