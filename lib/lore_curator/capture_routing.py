"""Capture-side routing: transcript registration and the linkage stamp.

The decision layer between a hook firing and the ledger. Hooks supply the
trigger and the adapter; everything here is about which transcripts that
trigger should register, and how much linkage to derive for them.

Registration is the end of the capture path. An entry records what was
captured, not work to be done: nothing reads it to compose a note, so this
module evaluates no threshold and launches no process.

Host-agnostic — no Claude-Code specifics, no CLI imports. The adapter is
passed in by the caller so this module never has to know what an integration
is.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lore_core.ledger import TranscriptLedger, TranscriptLedgerEntry

if TYPE_CHECKING:
    from lore_core.types import Scope





def register_pending_transcripts(
    lore_root: Path,
    cwd: Path,
    *,
    adapter: Any,
    transcript: Path | None = None,
    deep: bool = False,
) -> int:
    """List transcripts for ``cwd`` and upsert into the ledger.

    Returns the number of entries written.

    Shared by the capture hook (SessionStart/End/PreCompact) and
    ``user-prompt-submit`` (mid-session). Closes the SessionStart-vs-
    transcript-creation race for sessions whose transcript file did not
    exist when SessionStart sampled the directory: any subsequent
    UserPromptSubmit picks the missing entry up. mtime updates propagate
    so ``last_mtime`` stays current across a long session.

    Bulk-upserted in one ledger serialisation regardless of how many
    handles change — keeps the call well within the hook budget.

    ``deep`` promotes the linkage stamp from git state alone to a full
    transcript read (edited files, commit SHAs). Only a session boundary
    passes it; a per-prompt heartbeat must not pay that parse.
    """
    if transcript is not None:
        handles = [h for h in adapter.list_transcripts(cwd) if h.path == transcript]
    else:
        handles = adapter.list_transcripts(cwd)

    if not handles:
        return 0

    tledger = TranscriptLedger(lore_root)

    to_write: list[TranscriptLedgerEntry] = []
    for h in handles:
        entry = tledger.get(h.integration, h.id)
        if entry is None:
            to_write.append(
                TranscriptLedgerEntry(
                    integration=h.integration,
                    transcript_id=h.id,
                    path=h.path,
                    directory=h.cwd,
                    last_mtime=h.mtime,
                )
            )
        elif entry.last_mtime != h.mtime:
            entry.last_mtime = h.mtime
            to_write.append(entry)
    if to_write:
        _stamp_linkage(to_write, cwd, adapter=adapter, deep=deep)
        tledger.bulk_upsert(to_write)
    return len(to_write)


def _stamp_linkage(
    entries: list[TranscriptLedgerEntry],
    cwd: Path,
    *,
    adapter: Any,
    deep: bool,
) -> None:
    """Merge a freshly-derived linkage block into each entry, in place.

    Merged, not replaced: a shallow pass carries no ``commits``/``files``
    key, so the last deep pass's results survive it.

    Every entry in one call shares the same ``cwd``, so the git-derived
    half is derived once. The deep half is per-transcript and reads the
    file, which is why it only runs at a session boundary.
    """
    from lore_curator.ledger_linkage import build_linkage

    shallow = build_linkage(cwd)
    for entry in entries:
        block = dict(shallow)
        if deep:
            block.update(_deep_linkage(cwd, entry, adapter=adapter))
        entry.linkage = {**entry.linkage, **block}


def _deep_linkage(
    cwd: Path,
    entry: TranscriptLedgerEntry,
    *,
    adapter: Any,
) -> dict[str, Any]:
    """Linkage read out of one transcript, or ``{}`` if it cannot be read.

    ponytail: parses the whole transcript. Median cost is ~15 ms and the
    tail is ~300 ms on a 25 MB session, paid once per session boundary
    rather than per prompt. Read incrementally from the digested
    watermark if that tail ever shows up in hook telemetry.
    """
    from lore_core.types import TranscriptHandle

    from lore_curator.ledger_linkage import build_linkage

    try:
        handle = TranscriptHandle(
            integration=entry.integration,
            id=entry.transcript_id,
            path=entry.path,
            cwd=entry.directory,
            mtime=entry.last_mtime,
        )
        turns = list(adapter.read_slice(handle, 0))
    except Exception:  # noqa: BLE001 - linkage is derived; capture must not fail on it
        return {}
    return build_linkage(cwd, turns=turns)


@dataclass(frozen=True)
class CaptureRouting:
    """What :func:`route_capture` did, for the caller's telemetry."""

    outcome: str
    registered: int


def route_capture(
    lore_root: Path,
    cwd: Path,
    scope: Scope,
    *,
    event: str,
    adapter: Any,
    transcript: Path | None,
    progress: dict[str, Any] | None = None,
) -> CaptureRouting:
    """Register this cwd's transcripts in the ledger and report what it wrote.

    The decision core of the capture hook. Registration and the linkage stamp
    are the whole of it: nothing reads an entry to compose a note, so the
    hook evaluates no threshold and launches no process.

    ``registered`` counts the entries this call wrote — a new transcript, or
    one whose mtime moved. It reports what capture did, not a vault-wide
    backlog: no code consumes a backlog any more.

    ``progress`` is filled in as soon as the count is known, so a caller
    whose telemetry runs in an ``except`` block can still report it.
    """
    if progress is None:
        progress = {}

    # The boundary events are the only ones that promote capture to the
    # deep linkage pass: the transcript is complete there, and one full
    # parse per session is affordable where one per prompt is not.
    registered = register_pending_transcripts(
        lore_root, cwd, adapter=adapter, transcript=transcript,
        deep=event in ("session-end", "pre-compact"),
    )
    progress["registered"] = registered

    outcome = "captured" if registered > 0 else "no-new-turns"

    return CaptureRouting(outcome=outcome, registered=registered)
