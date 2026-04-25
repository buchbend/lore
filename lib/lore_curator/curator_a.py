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
from lore_core.run_log import RecordCallback, RunLogger
from lore_core.scope_resolver import resolve_scope
from lore_core.state.attachments import AttachmentsFile
from lore_core.types import Scope, Turn, TranscriptHandle
from lore_core.wiki_config import WikiConfig, load_wiki_config
from lore_curator.llm_client import LlmClientError
from lore_curator.noteworthy import classify_slice
from lore_curator.session_filer import FiledNote, file_session_note


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
    """

    transcripts_considered: int = 0
    chunks_considered: int = 0
    noteworthy_count: int = 0
    new_notes: list[Path] = field(default_factory=list)
    merged_notes: list[Path] = field(default_factory=list)
    skipped_reasons: dict[str, int] = field(default_factory=dict)
    duration_seconds: float = 0.0


def run_curator_a(
    *,
    lore_root: Path,
    scope: Scope | None = None,            # None = all attached scopes
    anthropic_client: Any = None,
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
      (unless anthropic_client is None). Dry-run bypasses the lockfile.
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
        for e in sorted(pending_snapshot, key=lambda x: (x.host, x.transcript_id)):
            h.update(f"{e.host}:{e.transcript_id}:{e.digested_hash or ''}\n".encode())
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
        if dry_run:
            # Dry-run bypasses the lockfile — must not block on a real run,
            # and writes nothing anyway.
            pending = tledger.pending(resolver=resolver)
            for entry in pending:
                result.transcripts_considered += 1
                outcomes = _process_entry(
                    entry,
                    tledger=tledger,
                    requested_scope=scope,
                    lore_root=lore_root,
                    lookup=lookup,
                    anthropic_client=anthropic_client,
                    dry_run=True,
                    now=now,
                    logger=logger,
                    resolver=resolver,
                )
                for outcome in outcomes:
                    _record_outcome(result, outcome)
                    if outcome.wiki_name is not None:
                        touched_wikis.add(outcome.wiki_name)
        else:
            try:
                with curator_lock(lore_root, timeout=lock_timeout, run_id=logger.run_id):
                    pending = tledger.pending(resolver=resolver)
                    for entry in pending:
                        result.transcripts_considered += 1
                        outcomes = _process_entry(
                            entry,
                            tledger=tledger,
                            requested_scope=scope,
                            lore_root=lore_root,
                            lookup=lookup,
                            anthropic_client=anthropic_client,
                            dry_run=False,
                            now=now,
                            logger=logger,
                            resolver=resolver,
                        )
                        for outcome in outcomes:
                            _record_outcome(result, outcome)
                            if outcome.wiki_name is not None:
                                touched_wikis.add(outcome.wiki_name)
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
    anthropic_client: Any,
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
                host=entry.host,
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
        adapter = lookup(entry.host)
    except Exception:
        if logger is not None:
            logger.emit("skip", transcript_id=entry.transcript_id, reason="unknown-host")
        return [_Outcome(skip_reason="unknown_host", wiki_name=attached.wiki)]

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
                host=entry.host,
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

    if anthropic_client is None:
        if logger is not None:
            logger.emit("skip", transcript_id=entry.transcript_id, reason="no-anthropic-client")
        return [_Outcome(skip_reason="no_anthropic_client", wiki_name=attached.wiki)]

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
        outcome = _process_chunk(
            chunk_turns,
            entry=entry,
            tledger=tledger,
            attached=attached,
            wiki_dir=wiki_dir,
            cfg=cfg,
            anthropic_client=anthropic_client,
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
    anthropic_client: Any,
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
            anthropic_client=anthropic_client,
            lore_root=lore_root,
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
                host=entry.host,
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
    )
    tledger.advance(
        host=entry.host,
        transcript_id=entry.transcript_id,
        digested_hash=last_hash,
        digested_index_hint=last_hint,
        noteworthy=True,
        session_note=filed.wikilink,
        curator_a_run=now,
    )

    if not dry_run:
        _maybe_auto_commit(wiki_dir, filed, logger)

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
    except Exception:
        pass

    return _Outcome(filed=filed, was_noteworthy=True, wiki_name=attached.wiki)


def _maybe_auto_commit(
    wiki_dir: Path,
    filed: "FiledNote",
    logger: "RunLogger | None" = None,
) -> None:
    """Git-add + commit the filed note if wiki config says auto_commit."""
    import subprocess
    from lore_core.wiki_config import load_wiki_config

    cfg = load_wiki_config(wiki_dir)
    if not cfg.git.auto_commit:
        return
    if not (wiki_dir / ".git").exists():
        return
    try:
        rel = filed.path.resolve().relative_to(wiki_dir.resolve())
        subprocess.run(
            ["git", "add", str(rel)],
            cwd=str(wiki_dir), capture_output=True, timeout=10, check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", f"lore: {filed.path.stem}"],
            cwd=str(wiki_dir), capture_output=True, timeout=10, check=False,
        )
    except Exception as exc:
        if logger is not None:
            logger.emit("warning", message=f"auto-commit failed: {exc}")


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
        host=e.host,
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


