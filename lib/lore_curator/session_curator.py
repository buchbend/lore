from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from lore_adapters import Adapter, get_adapter
from lore_core.ledger import (
    TranscriptLedger,
    TranscriptLedgerEntry,
    WikiLedger,
)
from lore_core.lockfile import curator_lock, LockContendedError, read_lock_holder
from lore_core.root_config import load_root_config
from lore_core.run_log import RecordCallback, RunLogger
from lore_core.scope_resolver import resolve_scope
from lore_core.state.attachments import AttachmentsFile
from lore_core.types import Scope, Turn, TranscriptHandle
from lore_core.wiki_config import WikiConfig, load_wiki_config
from lore_curator import stub_note
from lore_curator._auto_commit import maybe_auto_commit as _maybe_auto_commit
from lore_curator.buffer_append import append_chunk
from lore_curator.llm_client import LlmClientError
from lore_curator.noteworthy import classify_slice
from lore_curator.session_filer import FiledNote, _resolve_handle_for, file_session_note


Resolver = Callable[[Path], "Scope | None"]


def _build_resolver(lore_root: Path) -> Resolver:
    """Load the registry once per curator pass and bind it into a closure.

    All subsequent ``resolver(cwd)`` calls are O(log n) dict lookups with
    no filesystem I/O. When ``attachments.json`` is missing, the closure
    returns ``None`` for every cwd, which the curator surfaces as an
    ``__unattached__`` bucket.
    """
    attachments = AttachmentsFile(lore_root)
    attachments.load()

    def _resolver(cwd: Path) -> "Scope | None":
        return resolve_scope(cwd, attachments=attachments)

    return _resolver


def _refresh_ledger_mtimes(
    tledger: TranscriptLedger,
    lore_root: Path,
    lookup: Callable[[str], Adapter],
) -> None:
    """Stat transcript files and update stale last_mtime entries.

    The hook normally keeps mtimes current, but during a long session
    no hooks fire, so manual runs would see stale mtimes and 0 pending.
    """
    raw = tledger._load()
    to_write: list[TranscriptLedgerEntry] = []
    for key, raw_entry in raw.items():
        entry = tledger._entry_from_raw(raw_entry)
        if entry.orphan:
            continue
        p = entry.path
        if not p.exists():
            continue
        try:
            from datetime import UTC
            file_mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=UTC)
        except OSError:
            continue
        if file_mtime != entry.last_mtime:
            entry.last_mtime = file_mtime
            to_write.append(entry)
    if to_write:
        tledger.bulk_upsert(to_write)


@dataclass
class CuratorAResult:
    """Summary of one Curator A pass.

    Phase B distinguishes transcripts (one per ledger entry the curator
    considered) from chunks (one per local-day-bucket within each
    transcript — a 3-day transcript yields 3 chunks). The per-decision
    counters (``noteworthy_count``, ``skipped_reasons``, ``new_notes``,
    ``merged_notes``) increment per chunk, not per transcript.

    ``buffers_appended`` / ``buffers_flushed`` cover the buffer-and-flush
    path (``curator.use_buffer_flush``); they stay 0 under the legacy
    classify-per-chunk path.
    """

    transcripts_considered: int = 0
    chunks_considered: int = 0
    noteworthy_count: int = 0
    new_notes: list[Path] = field(default_factory=list)
    merged_notes: list[Path] = field(default_factory=list)
    skipped_reasons: dict[str, int] = field(default_factory=dict)
    duration_seconds: float = 0.0
    buffers_appended: int = 0
    buffers_flushed: int = 0


def _buffer_flush_enabled(lore_root: Path) -> bool:
    """Return True when the buffer-and-flush curator should drive heartbeats.

    Honours ``LORE_BUFFER_FLUSH=1`` env override (truthy values: ``1``,
    ``true``, ``yes`` — case-insensitive); otherwise falls back to
    ``$LORE_ROOT/.lore/config.yml`` ``curator.use_buffer_flush``.
    Default false until the plan's PR 3 stage flips the config.
    """
    env = os.environ.get("LORE_BUFFER_FLUSH", "")
    if env.strip().lower() in ("1", "true", "yes"):
        return True
    if env.strip().lower() in ("0", "false", "no"):
        return False
    try:
        return bool(load_root_config(lore_root).curator.use_buffer_flush)
    except Exception:  # noqa: BLE001 — never let config trip the curator
        return False


def _dispatch_flush_requested(
    lore_root: Path,
    *,
    llm_client: Any,
    logger: RunLogger,
    max_per_pass: int = 20,
) -> int:
    """Walk live buffers, run :func:`flush_buffer` for each ``flush_requested``.

    Called at the start of :func:`run_curator_a` (when buffer-flush is
    enabled) so SessionEnd / cap-trip / reaper handover unblocks before
    the curator-A pass touches the regular pending queue.

    Bounded by ``max_per_pass`` to keep one curator run from getting
    stuck on a runaway buffer queue. Each flush takes the per-buffer
    flock; runs are serialised within this process. Returns the count
    of flushes actually attempted.
    """
    from lore_curator.buffer_store import iter_all
    from lore_curator.synthesis import flush_buffer

    flushed = 0
    scanned = 0
    for buf in iter_all(lore_root):
        if scanned >= max_per_pass:
            break
        scanned += 1
        sidecar = buf.read_sidecar()
        if sidecar is None:
            continue
        if sidecar.state == "closed":
            continue
        if sidecar.flush_requested is None:
            continue
        wiki_dir = lore_root / "wiki" / sidecar.wiki
        try:
            cfg = load_wiki_config(wiki_dir)
        except Exception:  # noqa: BLE001
            continue
        tier = cfg.curator.synthesis_model_tier
        model = {"simple": cfg.models.simple, "middle": cfg.models.middle, "high": cfg.models.high}.get(
            tier, cfg.models.middle,
        )
        try:
            outcome = flush_buffer(
                buf.sidecar_path,
                lore_root=lore_root,
                wiki_root=wiki_dir,
                llm_client=llm_client,
                model=model,
                logger=logger,
            )
            flushed += 1
            logger.emit(
                "flush-requested-dispatched",
                buffer_stem=buf.stem,
                trigger=sidecar.flush_requested.trigger if sidecar.flush_requested else "",
                phase1=outcome.phase1_completed,
                phase2=outcome.phase2_completed,
                degraded=outcome.degraded,
            )
        except Exception as exc:  # noqa: BLE001 - never abort curator-A on a flush failure
            logger.emit(
                "warning",
                call="flush-requested-dispatch",
                buffer_stem=buf.stem,
                message=f"{type(exc).__name__}: {exc}",
            )
    return flushed


def _resolve_active_part(
    lore_root: Path,
    *,
    transcript_id: str,
    local_date: str,
) -> tuple[int, str | None]:
    """Pick the ``part_index`` and (if continuing) the prior part's stem.

    Algorithm:
    1. If any live (non-_done) sidecar exists for this
       ``(transcript_id, local_date)`` and is still ``accumulating``,
       reuse its ``part_index`` (we're appending into the active part).
    2. Otherwise find the highest ``part_index`` seen for this pair —
       across any state — and return ``(highest + 1,
       <highest's stem>)``. This is the cap-trip continuation case.
    3. Fresh pair: ``(1, None)``.
    """
    from lore_curator.buffer_store import iter_all

    compact = local_date.replace("-", "")
    prefix = f"{transcript_id}__{compact}"
    candidates: list[tuple[int, str, str]] = []  # (part_index, state, stem)
    for buf in iter_all(lore_root):
        if not buf.stem.startswith(prefix):
            continue
        sidecar = buf.read_sidecar()
        if sidecar is None:
            continue
        if sidecar.transcript_id != transcript_id or sidecar.local_date != local_date:
            continue
        candidates.append((sidecar.part_index, sidecar.state, buf.stem))

    if not candidates:
        return 1, None

    # Active part: still accumulating.
    accumulating = [c for c in candidates if c[1] == "accumulating"]
    if accumulating:
        # Defensive: pick the highest part_index in case multiple exist
        # (shouldn't happen — cap-trip flips the prior to ready before
        # opening a new one).
        accumulating.sort(reverse=True)
        return accumulating[0][0], None

    # Continuation case: open Part-(highest + 1).
    candidates.sort(reverse=True)
    highest_part, _state, highest_stem = candidates[0]
    return highest_part + 1, highest_stem


def _chunk_local_date(chunk_turns: list[Turn]) -> str:
    """Return the chunk's local date as ``YYYY-MM-DD``.

    Chunks are produced by ``_split_turns_by_local_date``, so every
    timestamped turn within one chunk shares the same local date. The
    fallback for an all-untimestamped chunk is "today" (local) — matches
    the legacy filer's ``work_time = handle.mtime or now`` shape.
    """
    for t in chunk_turns:
        if t.timestamp is None:
            continue
        ts = t.timestamp if t.timestamp.tzinfo is not None else t.timestamp.replace(tzinfo=UTC)
        return ts.astimezone().date().isoformat()
    return datetime.now().astimezone().date().isoformat()


def run_curator_a(
    *,
    lore_root: Path,
    scope: Scope | None = None,            # None = all attached scopes
    llm_client: Any = None,
    adapter_lookup: Callable[[str], Adapter] | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
    lock_timeout: float = 0.0,             # interactive callers pass >0 to wait
    trigger: str = "hook",
    trace_llm: bool = False,
    on_record: RecordCallback | None = None,
) -> CuratorAResult:
    """Run Curator A one pass.

    - Acquires the lockfile at `<lore_root>/.lore/curator.lock`.
    - Reads the sidecar transcript ledger.
    - For each pending entry whose `directory` resolves to an attached
      scope (or matches the supplied `scope`), loads new turns via its
      adapter, redacts, classifies via `classify_slice`, and on
      noteworthy=True files a session note via `file_session_note`.
    - Advances the ledger for every considered transcript (noteworthy
      or not) so we don't re-process.
    - `dry_run=True` skips all writes (including ledger advance and
      session-note file creation) but still runs the classification
      (unless llm_client is None). Dry-run bypasses the lockfile.
    """
    start = time.monotonic()
    now = now or datetime.now(UTC)
    result = CuratorAResult()

    lookup = adapter_lookup or get_adapter
    tledger = TranscriptLedger(lore_root)
    resolver = _build_resolver(lore_root)

    if trigger == "manual":
        _refresh_ledger_mtimes(tledger, lore_root, lookup)

    pending_snapshot = tledger.pending(resolver=resolver)

    config_snapshot = {"noteworthy_tier": "middle"}
    effective_trigger = "dry-run" if dry_run else trigger

    # Compute a ledger snapshot hash for dry-runs so divergent output is debuggable.
    ledger_snapshot_hash = None
    if dry_run:
        import hashlib
        h = hashlib.sha256()
        for e in sorted(pending_snapshot, key=lambda x: (x.integration, x.transcript_id)):
            h.update(f"{e.integration}:{e.transcript_id}:{e.digested_hash or ''}\n".encode())
        ledger_snapshot_hash = h.hexdigest()[:16]

    touched_wikis: set[str] = set()

    with RunLogger(
        lore_root,
        trigger=effective_trigger,
        pending_count=len(pending_snapshot),
        config_snapshot=config_snapshot,
        dry_run=dry_run,
        trace_llm=trace_llm,
        ledger_snapshot_hash=ledger_snapshot_hash,
        on_record=on_record,
    ) as logger:
        def _iterate_pending() -> None:
            """Inner loop body — shared by dry-run and locked paths.

            Closures over the locals so the two call sites stay one line
            each; pre-Phase-11 they were ~22 lines of nearly-identical
            code that drifted the moment a bug fix touched only one
            branch (the same shape the Phase 6 ``run_curator_c``
            decomposition targeted, missed in this function).
            """
            pending = tledger.pending(resolver=resolver)
            for entry in pending:
                result.transcripts_considered += 1
                outcomes = _process_entry(
                    entry,
                    tledger=tledger,
                    requested_scope=scope,
                    lore_root=lore_root,
                    lookup=lookup,
                    llm_client=llm_client,
                    dry_run=dry_run,
                    now=now,
                    logger=logger,
                    resolver=resolver,
                )
                for outcome in outcomes:
                    _record_outcome(result, outcome)
                    if outcome.wiki_name is not None:
                        touched_wikis.add(outcome.wiki_name)

        if dry_run:
            # Dry-run bypasses the lockfile — must not block on a real run,
            # and writes nothing anyway.
            _iterate_pending()
        else:
            try:
                with curator_lock(lore_root, timeout=lock_timeout, run_id=logger.run_id):
                    # Buffer-and-flush handover dispatch first so a
                    # SessionEnd / PreCompact / cap-trip flush_requested
                    # marker unblocks before the regular pending loop.
                    if _buffer_flush_enabled(lore_root):
                        try:
                            _dispatch_flush_requested(
                                lore_root,
                                llm_client=llm_client,
                                logger=logger,
                            )
                        except Exception as exc:  # noqa: BLE001
                            logger.emit(
                                "warning",
                                call="flush-dispatch",
                                message=f"{type(exc).__name__}: {exc}",
                            )
                    _iterate_pending()
                    # Inline reaper pass — bounded so the spawn overhead
                    # stays sub-100ms even with many orphan buffers.
                    # Behind the same flag as the heartbeat path so
                    # non-buffer-flush deployments stay untouched.
                    if _buffer_flush_enabled(lore_root):
                        try:
                            from lore_curator.reaper import reap_once

                            reap_once(
                                lore_root,
                                max_per_pass=5,
                                logger=logger,
                            )
                        except Exception as exc:  # noqa: BLE001 — reaper must never abort curator-A
                            logger.emit(
                                "warning",
                                call="reaper-inline",
                                message=f"{type(exc).__name__}: {exc}",
                            )
                # Only update last_curator_a on successful run completion.
                # On dry-run: skip (telemetry is only for real runs).
                # On mid-run exception: this line is unreachable, prior value
                # preserved — atomic-or-unchanged contract.
                for wname in touched_wikis:
                    WikiLedger(lore_root, wname).update_last_curator("a", at=now)
            except LockContendedError:
                result.skipped_reasons["lock_contended"] = (
                    result.skipped_reasons.get("lock_contended", 0) + 1
                )
                holder = read_lock_holder(lore_root)
                holder_pid = holder.get("pid") if holder else None
                holder_run_id = holder.get("run_id") if holder else None
                holder_started_at = holder.get("started_at") if holder else None
                holder_age_s = None
                if holder_started_at:
                    try:
                        started = datetime.fromisoformat(holder_started_at.replace("Z", "+00:00"))
                        if started.tzinfo is None:
                            started = started.replace(tzinfo=UTC)
                        holder_age_s = int((datetime.now(UTC) - started).total_seconds())
                    except ValueError:
                        pass
                logger.emit(
                    "skip",
                    reason="lock-held",
                    holder_pid=holder_pid,
                    holder_run_id=holder_run_id,
                    holder_age_s=holder_age_s,
                )

    result.duration_seconds = time.monotonic() - start
    return result


@dataclass
class _Outcome:
    skip_reason: str | None = None          # if set, no session-note path follows
    filed: FiledNote | None = None
    was_noteworthy: bool = False
    wiki_name: str | None = None            # wiki the entry resolved into (None if unattached)


def _split_turns_by_local_date(turns: list[Turn]) -> list[list[Turn]]:
    """Partition turns into contiguous runs sharing the same local date.

    "Local" — not UTC — because "what did I work on yesterday" is anchored
    to the user's wall clock. Turns are processed in order; when the local
    date of a timestamped turn changes, a new chunk starts. Turns without
    timestamps stick to the chunk currently being built (so a malformed
    transcript missing one timestamp doesn't fragment the slice). When all
    turns lack timestamps the whole slice is one chunk — preserving
    pre-Phase-B behaviour for tests / fixtures / older transcripts.
    """
    if not turns:
        return []
    if all(t.timestamp is None for t in turns):
        return [list(turns)]

    chunks: list[list[Turn]] = []
    current: list[Turn] = []
    current_date = None
    for t in turns:
        if t.timestamp is None:
            current.append(t)
            continue
        # Naive timestamps could come from a future host that forgot to
        # set tzinfo — treat them as UTC so .astimezone() has a defined
        # behaviour regardless of platform / Python version.
        ts = t.timestamp if t.timestamp.tzinfo is not None else t.timestamp.replace(tzinfo=UTC)
        local = ts.astimezone().date()
        if current_date is None or local == current_date:
            current_date = local
            current.append(t)
        else:
            chunks.append(current)
            current = [t]
            current_date = local
    if current:
        chunks.append(current)
    return chunks



def _process_entry(
    entry: TranscriptLedgerEntry,
    *,
    tledger: TranscriptLedger,
    requested_scope: Scope | None,
    lore_root: Path,
    lookup: Callable[[str], Adapter],
    llm_client: Any,
    dry_run: bool,
    now: datetime,
    logger: RunLogger | None = None,
    resolver: Resolver | None = None,
) -> list[_Outcome]:
    """Return one or more outcomes per transcript.

    Phase B: a single transcript whose new turns span multiple local
    days produces one outcome per day. The ledger advances per chunk so
    a mid-run failure leaves earlier chunks safely committed and the
    rest pending for the next heartbeat.
    """
    # Orphan cwd: the directory the transcript was captured in no longer
    # exists. Mark the entry as orphan and stamp curator_a_run so it
    # never resurfaces in pending().
    if not entry.directory.exists():
        if not dry_run:
            tledger.stamp_scan(
                integration=entry.integration,
                transcript_id=entry.transcript_id,
                curator_a_run=now,
                orphan=True,
            )
        if logger is not None:
            logger.emit("skip", transcript_id=entry.transcript_id, reason="orphan-cwd")
        return [_Outcome(skip_reason="orphan_cwd")]

    # Resolve scope from the transcript's directory; must be attached.
    # Uses the injected resolver (registry-backed longest-prefix match).
    _resolve = resolver if resolver is not None else resolve_scope
    attached = _resolve(entry.directory)
    if attached is None:
        if logger is not None:
            logger.emit("skip", transcript_id=entry.transcript_id, reason="unattached")
        return [_Outcome(skip_reason="unattached")]

    if requested_scope is not None and attached.scope != requested_scope.scope:
        if logger is not None:
            logger.emit("skip", transcript_id=entry.transcript_id, reason="scope-mismatch")
        return [_Outcome(skip_reason="scope_mismatch", wiki_name=attached.wiki)]

    # Adapter lookup
    try:
        adapter = lookup(entry.integration)
    except Exception:  # noqa: BLE001 - lookup is pluggable; treat any failure as unknown-integration
        if logger is not None:
            logger.emit("skip", transcript_id=entry.transcript_id, reason="unknown-integration")
        return [_Outcome(skip_reason="unknown_integration", wiki_name=attached.wiki)]

    if logger is not None:
        logger.emit(
            "transcript-start",
            transcript_id=entry.transcript_id,
            hash_before=entry.digested_hash,
            new_turns=0,  # approximate until Plan 3 breadcrumb drain exists
        )

    handle = _handle_from_entry(entry)
    turns = list(
        adapter.read_slice_after_hash(
            handle,
            after_hash=entry.digested_hash,
            index_hint=entry.digested_index_hint,
        )
    )
    if not turns:
        # Nothing new since last digest — advance ledger's mtime-only state and move on.
        if not dry_run:
            tledger.advance(
                integration=entry.integration,
                transcript_id=entry.transcript_id,
                digested_hash=entry.digested_hash or "",
                digested_index_hint=entry.digested_index_hint or 0,
                noteworthy=bool(entry.noteworthy),
                session_note=entry.session_note,
                curator_a_run=now,
            )
        if logger is not None:
            logger.emit("skip", transcript_id=entry.transcript_id, reason="no-new-turns")
        return [_Outcome(skip_reason="no_new_turns", wiki_name=attached.wiki)]

    wiki_dir = lore_root / "wiki" / attached.wiki
    cfg = load_wiki_config(wiki_dir)

    if llm_client is None:
        if logger is not None:
            logger.emit("skip", transcript_id=entry.transcript_id, reason="no-llm-client")
        return [_Outcome(skip_reason="no_llm_client", wiki_name=attached.wiki)]

    # Cross-scope bleed guard runs over the full slice (not per chunk) —
    # the work happened in whatever wiki the file paths point at, regardless
    # of how the slice gets split across days.
    file_paths = _extract_tool_file_paths(turns)
    override = _detect_scope_override(file_paths, attached, _resolve)
    scope_redirected_from: str | None = None
    if override is not None:
        if logger is not None:
            logger.emit(
                "scope-redirect",
                transcript_id=entry.transcript_id,
                from_scope=attached.scope,
                to_scope=override.scope,
                to_wiki=override.wiki,
            )
        scope_redirected_from = attached.scope
        attached = override
        wiki_dir = lore_root / "wiki" / attached.wiki
        cfg = load_wiki_config(wiki_dir)

    chunks = _split_turns_by_local_date(turns)
    outcomes: list[_Outcome] = []
    for chunk_turns in chunks:
        if _buffer_flush_enabled(lore_root):
            outcome = _process_chunk_buffer_flush(
                chunk_turns,
                entry=entry,
                tledger=tledger,
                attached=attached,
                wiki_dir=wiki_dir,
                cfg=cfg,
                handle=handle,
                now=now,
                dry_run=dry_run,
                logger=logger,
                lore_root=lore_root,
                scope_redirected_from=scope_redirected_from,
            )
        else:
            outcome = _process_chunk(
                chunk_turns,
                entry=entry,
                tledger=tledger,
                attached=attached,
                wiki_dir=wiki_dir,
                cfg=cfg,
                llm_client=llm_client,
                handle=handle,
                now=now,
                dry_run=dry_run,
                logger=logger,
                lore_root=lore_root,
                scope_redirected_from=scope_redirected_from,
            )
        outcomes.append(outcome)
        # Per-chunk failure isolation: if classify_slice failed for THIS
        # chunk we must not process later chunks. A later chunk that
        # succeeds would advance the ledger past this chunk's last hash
        # and the failed content would be lost forever. Subsequent
        # chunks stay pending; next heartbeat retries from this chunk.
        if outcome.skip_reason and outcome.skip_reason.startswith("classify_failed"):
            break
    return outcomes


def _process_chunk(
    chunk_turns: list[Turn],
    *,
    entry: TranscriptLedgerEntry,
    tledger: TranscriptLedger,
    attached: Scope,
    wiki_dir: Path,
    cfg: WikiConfig,
    llm_client: Any,
    handle: TranscriptHandle,
    now: datetime,
    dry_run: bool,
    logger: RunLogger | None,
    lore_root: Path,
    scope_redirected_from: str | None,
) -> _Outcome:
    """Classify + file one chunk (post Phase-B day split).

    Each chunk is a contiguous run of turns in the same local date. The
    ledger advances to this chunk's last hash on completion so the next
    chunk (or run) picks up cleanly.
    """
    tier = cfg.curator.a_noteworthy_tier

    def model_resolver(t: str) -> str:
        return {"simple": cfg.models.simple, "middle": cfg.models.middle, "high": cfg.models.high}[t]

    try:
        noteworthy = classify_slice(
            chunk_turns,
            tier=tier,
            model_resolver=model_resolver,
            llm_client=llm_client,
            lore_root=lore_root,
            wiki_dir=wiki_dir,
            logger=logger,
            transcript_id=entry.transcript_id,
        )
    except LlmClientError as exc:
        # Per-chunk isolation: a 5xx / timeout / oversize-prompt failure
        # on one chunk must not block the rest of the slice. Ledger stays
        # un-advanced for THIS chunk so it's retried next run.
        if logger is not None:
            logger.emit(
                "skip",
                transcript_id=entry.transcript_id,
                reason="classify-failed",
                error=str(exc)[:300],
            )
        return _Outcome(
            skip_reason=f"classify_failed:{type(exc).__name__}",
            wiki_name=attached.wiki,
        )

    last_hash = chunk_turns[-1].content_hash()
    last_hint = chunk_turns[-1].index

    if not noteworthy.noteworthy:
        if not dry_run:
            tledger.advance(
                integration=entry.integration,
                transcript_id=entry.transcript_id,
                digested_hash=last_hash,
                digested_index_hint=last_hint,
                noteworthy=False,
                session_note=None,
                curator_a_run=now,
            )
        if logger is not None:
            logger.emit("skip", transcript_id=entry.transcript_id, reason="noteworthy-false")
        return _Outcome(
            skip_reason=f"not_noteworthy:{noteworthy.reason}",
            was_noteworthy=False,
            wiki_name=attached.wiki,
        )

    if dry_run:
        return _Outcome(was_noteworthy=True, wiki_name=attached.wiki)

    work_time = chunk_turns[-1].timestamp or handle.mtime or now

    filed = file_session_note(
        scope=attached,
        handle=handle,
        noteworthy=noteworthy,
        turns=chunk_turns,
        wiki_root=wiki_dir,
        now=now,
        work_time=work_time,
        logger=logger,
        transcript_id=entry.transcript_id,
        scope_redirected_from=scope_redirected_from,
        # LLM-merged summary on append: same client + tier as
        # noteworthy classification. Reusing ``middle`` keeps the
        # quality bar consistent — both calls are short, structured,
        # and benefit from the same tier-budget tuning. The writer
        # short-circuits on new-note path (no merge needed) so the
        # extra round-trip only fires when there's an actual append.
        llm_client=llm_client,
        summary_merge_model=cfg.models.middle,
    )
    tledger.advance(
        integration=entry.integration,
        transcript_id=entry.transcript_id,
        digested_hash=last_hash,
        digested_index_hint=last_hint,
        noteworthy=True,
        session_note=filed.wikilink,
        curator_a_run=now,
    )

    if not dry_run:
        _maybe_auto_commit(wiki_dir, filed, logger, llm_client=llm_client)

    try:
        from lore_core.drain import DrainStore, resolve_session_id

        sid, _ = resolve_session_id(entry.directory)
        DrainStore(lore_root, sid).emit(
            "note-appended" if filed.was_merge else "note-filed",
            wiki=attached.wiki,
            wikilink=filed.wikilink,
            path=str(filed.path),
            transcript_id=entry.transcript_id,
        )
    except Exception:  # noqa: BLE001 - logger emit must never abort a successful file
        pass

    return _Outcome(filed=filed, was_noteworthy=True, wiki_name=attached.wiki)


def _process_chunk_buffer_flush(
    chunk_turns: list[Turn],
    *,
    entry: TranscriptLedgerEntry,
    tledger: TranscriptLedger,
    attached: Scope,
    wiki_dir: Path,
    cfg: WikiConfig,
    handle: TranscriptHandle,
    now: datetime,
    dry_run: bool,
    logger: RunLogger | None,
    lore_root: Path,
    scope_redirected_from: str | None,
) -> _Outcome:
    """Buffer-and-flush variant of :func:`_process_chunk`.

    No ``classify_slice``, no per-chunk LLM call. Each heartbeat:

    1. Open / load the ``(transcript_id, local_date)`` buffer and append
       a deterministic event (slice pointers + Activity bullets +
       files_touched / plans / projects deltas).
    2. Write or rewrite the live stub note at the canonical session
       path. Body shows the running deterministic accumulator;
       ``state: stub`` frontmatter marks it as in-flight.
    3. Advance the transcript ledger to the chunk's last hash so the
       next heartbeat picks up where this one left off.

    The flush worker (synthesis.py — Step 4) finalises the stub at
    SessionEnd / cap-trip / reaper. This function never invokes it
    inline — handover beats optimisation at the heartbeat path.
    """
    if dry_run:
        return _Outcome(was_noteworthy=True, wiki_name=attached.wiki)

    work_time = chunk_turns[-1].timestamp or handle.mtime or now
    local_date = _chunk_local_date(chunk_turns)
    handle_label = _resolve_handle_for(wiki_dir, handle)
    part_index, continuation_of = _resolve_active_part(
        lore_root,
        transcript_id=entry.transcript_id,
        local_date=local_date,
    )

    outcome = append_chunk(
        lore_root=lore_root,
        chunk_turns=chunk_turns,
        local_date=local_date,
        transcript_id=entry.transcript_id,
        integration=entry.integration,
        wiki=attached.wiki,
        scope=attached.scope,
        cwd=handle.cwd,
        wiki_root=wiki_dir,
        cfg=cfg,
        handle_label=handle_label,
        owner_run_id=getattr(logger, "run_id", "") or "",
        owner_claude_session_id=os.environ.get("CLAUDE_SESSION_ID", ""),
        logger=logger,
        part_index=part_index,
        continuation_of=continuation_of,
    )

    if outcome.skipped_no_op:
        # Empty chunk — advance the ledger anyway so the entry doesn't
        # re-surface as pending forever.
        last_hash = chunk_turns[-1].content_hash() if chunk_turns else (entry.digested_hash or "")
        last_hint = chunk_turns[-1].index if chunk_turns else (entry.digested_index_hint or 0)
        tledger.advance(
            integration=entry.integration,
            transcript_id=entry.transcript_id,
            digested_hash=last_hash,
            digested_index_hint=last_hint,
            noteworthy=bool(entry.noteworthy),
            session_note=entry.session_note,
            curator_a_run=now,
        )
        return _Outcome(was_noteworthy=False, wiki_name=attached.wiki)

    chunk_from_hash = chunk_turns[0].content_hash()
    chunk_to_hash = chunk_turns[-1].content_hash()

    stub_result = stub_note.write_or_update(
        outcome=outcome,
        scope=attached,
        transcript=handle,
        wiki_root=wiki_dir,
        work_time=work_time,
        now=now,
        integration=entry.integration,
        handle_label=handle_label,
        chunk_from_hash=chunk_from_hash,
        chunk_to_hash=chunk_to_hash,
        logger=logger,
    )

    wikilink: str | None = stub_result.wikilink if stub_result is not None else None

    tledger.advance(
        integration=entry.integration,
        transcript_id=entry.transcript_id,
        digested_hash=outcome.last_hash,
        digested_index_hint=outcome.last_index,
        noteworthy=True,
        session_note=wikilink,
        curator_a_run=now,
    )

    if outcome.cap_tripped:
        # Spawn the flush worker for the just-tripped part. The next
        # heartbeat for this transcript+date will land in Part-N+1
        # (resolved via _resolve_active_part). We don't open Part-N+1
        # eagerly — cap-trip flipped the current buffer to ``ready``,
        # so the next call hitting _resolve_active_part skips it and
        # falls into the continuation branch.
        from lore_curator.synthesis import spawn_detached_flush

        spawned = spawn_detached_flush(
            outcome.buffer.sidecar_path, lore_root=lore_root,
        )
        if logger is not None:
            logger.emit(
                "flush-spawned",
                trigger="cap-trip",
                transcript_id=entry.transcript_id,
                buffer_stem=outcome.buffer.stem,
                spawned=spawned,
            )

    if stub_result is not None:
        filed = FiledNote(
            path=stub_result.path,
            wikilink=stub_result.wikilink,
            was_merge=not stub_result.is_first_write,
        )
        _maybe_auto_commit(wiki_dir, filed, logger, llm_client=None)

        try:
            from lore_core.drain import DrainStore, resolve_session_id

            sid, _ = resolve_session_id(entry.directory)
            DrainStore(lore_root, sid).emit(
                "note-appended" if not stub_result.is_first_write else "note-filed",
                wiki=attached.wiki,
                wikilink=stub_result.wikilink,
                path=str(stub_result.path),
                transcript_id=entry.transcript_id,
            )
        except Exception:  # noqa: BLE001 — drain emit must never abort a successful append
            pass

        if scope_redirected_from is not None and logger is not None:
            logger.emit(
                "scope-redirected-stub",
                transcript_id=entry.transcript_id,
                from_scope=scope_redirected_from,
                to_scope=attached.scope,
            )

        return _Outcome(filed=filed, was_noteworthy=True, wiki_name=attached.wiki)

    return _Outcome(was_noteworthy=True, wiki_name=attached.wiki)


def _record_outcome(result: CuratorAResult, outcome: _Outcome) -> None:
    # Every outcome corresponds to one decision unit (one chunk).
    # `transcripts_considered` is incremented at the entry level by the
    # caller; here we count chunks so the two telemetry axes stay
    # independent.
    result.chunks_considered += 1
    if outcome.filed is not None:
        result.noteworthy_count += 1
        if outcome.filed.was_merge:
            result.merged_notes.append(outcome.filed.path)
        else:
            result.new_notes.append(outcome.filed.path)
    elif outcome.was_noteworthy:
        # dry_run noteworthy path — still count
        result.noteworthy_count += 1
    if outcome.skip_reason is not None:
        reason = outcome.skip_reason.split(":", 1)[0]  # collapse not_noteworthy:<long>
        result.skipped_reasons[reason] = result.skipped_reasons.get(reason, 0) + 1


def _handle_from_entry(e: TranscriptLedgerEntry) -> TranscriptHandle:
    return TranscriptHandle(
        integration=e.integration,
        id=e.transcript_id,
        path=e.path,
        cwd=e.directory,
        mtime=e.last_mtime,
    )


_FILE_PATH_TOOLS = frozenset({"Read", "Write", "Edit"})
_REDIRECT_THRESHOLD = 0.6


def _extract_tool_file_paths(turns: list[Turn]) -> list[Path]:
    """Extract absolute file paths from file-manipulation tool calls."""
    paths: list[Path] = []
    for t in turns:
        if t.tool_call is None or t.tool_call.name not in _FILE_PATH_TOOLS:
            continue
        fp = t.tool_call.input.get("file_path")
        if not fp or not isinstance(fp, str):
            continue
        p = Path(fp)
        if not p.is_absolute():
            continue
        s = str(p)
        if s.startswith(("/tmp/", "/dev/", "/proc/")):
            continue
        paths.append(p)
    return paths


def _detect_scope_override(
    file_paths: list[Path],
    launch_scope: Scope,
    resolver: Resolver,
) -> Scope | None:
    """Return an override Scope when ≥60% of file paths resolve to a different wiki."""
    if not file_paths:
        return None
    wiki_counts: dict[str, int] = {}
    scope_for_wiki: dict[str, Scope] = {}
    for p in file_paths:
        s = resolver(p)
        if s is None:
            continue
        wiki_counts[s.wiki] = wiki_counts.get(s.wiki, 0) + 1
        if s.wiki not in scope_for_wiki or len(s.scope) > len(scope_for_wiki[s.wiki].scope):
            scope_for_wiki[s.wiki] = s
    if not wiki_counts:
        return None
    total = sum(wiki_counts.values())
    for wiki, count in sorted(wiki_counts.items(), key=lambda x: -x[1]):
        if wiki == launch_scope.wiki:
            continue
        if count / total >= _REDIRECT_THRESHOLD:
            return scope_for_wiki[wiki]
    return None



