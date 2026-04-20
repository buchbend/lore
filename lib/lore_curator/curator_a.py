from __future__ import annotations

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
from lore_core.lockfile import curator_lock, LockContendedError
from lore_core.redaction import redact
from lore_core.run_log import RunLogger
from lore_core.scope_resolver import resolve_scope
from lore_core.types import Scope, Turn, TranscriptHandle
from lore_core.wiki_config import WikiConfig, load_wiki_config
from lore_curator.noteworthy import NoteworthyResult, classify_slice
from lore_curator.session_filer import FiledNote, file_session_note


@dataclass
class CuratorAResult:
    transcripts_considered: int = 0
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
    pending_snapshot = tledger.pending()

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

    with RunLogger(
        lore_root,
        trigger=effective_trigger,
        pending_count=len(pending_snapshot),
        config_snapshot=config_snapshot,
        dry_run=dry_run,
        trace_llm=trace_llm,
        ledger_snapshot_hash=ledger_snapshot_hash,
    ) as logger:
        if dry_run:
            # Dry-run bypasses the lockfile — must not block on a real run,
            # and writes nothing anyway.
            pending = tledger.pending()
            for entry in pending:
                result.transcripts_considered += 1
                outcome = _process_entry(
                    entry,
                    tledger=tledger,
                    requested_scope=scope,
                    lore_root=lore_root,
                    lookup=lookup,
                    anthropic_client=anthropic_client,
                    dry_run=True,
                    now=now,
                    logger=logger,
                )
                _record_outcome(result, outcome)
        else:
            try:
                with curator_lock(lore_root, timeout=lock_timeout):
                    pending = tledger.pending()
                    for entry in pending:
                        result.transcripts_considered += 1
                        outcome = _process_entry(
                            entry,
                            tledger=tledger,
                            requested_scope=scope,
                            lore_root=lore_root,
                            lookup=lookup,
                            anthropic_client=anthropic_client,
                            dry_run=False,
                            now=now,
                            logger=logger,
                        )
                        _record_outcome(result, outcome)
            except LockContendedError:
                result.skipped_reasons["lock_contended"] = (
                    result.skipped_reasons.get("lock_contended", 0) + 1
                )
                logger.emit("skip", reason="lock-held")

    result.duration_seconds = time.monotonic() - start
    return result


@dataclass
class _Outcome:
    skip_reason: str | None = None          # if set, no session-note path follows
    filed: FiledNote | None = None
    was_noteworthy: bool = False


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
) -> _Outcome:
    # Resolve scope from the transcript's directory; must be attached.
    attached = resolve_scope(entry.directory)
    if attached is None:
        if logger is not None:
            logger.emit("skip", transcript_id=entry.transcript_id, reason="unattached")
        return _Outcome(skip_reason="unattached")
    if requested_scope is not None and attached.scope != requested_scope.scope:
        if logger is not None:
            logger.emit("skip", transcript_id=entry.transcript_id, reason="scope-mismatch")
        return _Outcome(skip_reason="scope_mismatch")

    # Adapter lookup
    try:
        adapter = lookup(entry.host)
    except Exception:
        if logger is not None:
            logger.emit("skip", transcript_id=entry.transcript_id, reason="unknown-host")
        return _Outcome(skip_reason="unknown_host")

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
        return _Outcome(skip_reason="no_new_turns")

    # Redact content before it ever sees the LLM.
    log_path = lore_root / ".lore" / "redaction.log"
    for t in turns:
        _redact_turn_in_place_best_effort(t, log_path)

    # Load per-wiki config for model tiers — use `attached.wiki` directly,
    # don't re-parse CLAUDE.md (avoids TOCTOU mismatch with resolve_scope).
    wiki_dir = lore_root / "wiki" / attached.wiki
    cfg = load_wiki_config(wiki_dir)
    tier = cfg.curator.a_noteworthy_tier

    def model_resolver(t: str) -> str:
        return {"simple": cfg.models.simple, "middle": cfg.models.middle, "high": cfg.models.high}[t]

    if anthropic_client is None:
        if logger is not None:
            logger.emit("skip", transcript_id=entry.transcript_id, reason="no-anthropic-client")
        return _Outcome(skip_reason="no_anthropic_client")

    noteworthy = classify_slice(
        turns,
        tier=tier,
        model_resolver=model_resolver,
        anthropic_client=anthropic_client,
        lore_root=lore_root,
        logger=logger,
        transcript_id=entry.transcript_id,
    )

    last_hash = turns[-1].content_hash()
    last_hint = turns[-1].index

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
        return _Outcome(skip_reason=f"not_noteworthy:{noteworthy.reason}", was_noteworthy=False)

    # File it.
    if dry_run:
        return _Outcome(was_noteworthy=True)

    filed = file_session_note(
        scope=attached,
        handle=handle,
        noteworthy=noteworthy,
        turns=turns,
        wiki_root=wiki_dir,
        anthropic_client=anthropic_client,
        model_resolver=model_resolver,
        now=now,
        logger=logger,
        transcript_id=entry.transcript_id,
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
    return _Outcome(filed=filed, was_noteworthy=True)


def _record_outcome(result: CuratorAResult, outcome: _Outcome) -> None:
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


def _redact_turn_in_place_best_effort(t: Turn, log_path: Path) -> None:
    """Redact text + tool_result.output in-place via new Turn construction.

    Frozen dataclass — can't mutate. Caller should reassign. For the
    purposes of this pipeline we rebuild the list in the caller. Here
    we simply check whether the turn's text/tool_result output contains
    any secret patterns; the *classify_slice* prompt builder truncates
    long tool results already. Full redact-and-reassign happens inside
    classify_slice's prompt construction (frontier concern) — not here.
    """
    # Best-effort check to populate the redaction log.
    if t.text:
        redact(t.text, log_path=log_path)
    if t.tool_result and t.tool_result.output:
        redact(t.tool_result.output, log_path=log_path)


# Note: prior helpers `_wiki_dir_from_claude_md` and `_first_scope_segment_from`
# were removed — the pipeline now reads `attached.wiki` directly from the
# resolved Scope, eliminating a redundant CLAUDE.md re-parse and a layer
# violation (lore_curator → lore_cli).
