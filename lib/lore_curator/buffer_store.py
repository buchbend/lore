"""Per-(transcript_id, local_date) buffer for the buffer-and-flush curator.

Replaces per-chunk synthesise-and-merge with: deterministic accumulation
into a JSONL event log + small JSON state-sidecar, synthesised once at
flush time. See plan ``very-good-thats-the-mossy-lobster`` and the
context block of this module's tests for the architectural narrative.

Layout under ``<lore_root>/.lore/buffers/``::

    <stem>.state.json   small sidecar; rewritten only on transitions
    <stem>.jsonl        append-only event log; one line per heartbeat
    <stem>.lock         per-buffer flock used by every mutation

where ``<stem> = <transcript_id>__<YYYYMMDD>``. There is one buffer per
``(transcript_id, local_date)`` and it yields one session note. On close,
both ``.state.json`` and ``.jsonl`` are moved to ``.lore/buffers/_done/``.

State machine:

    (missing) -> accumulating -> ready -> flushing -> closed
                       ^           ^
                       |           |
                       +-- append -+

``flush_attempts`` for Phase 2 LLM retries is bumped *inside* the
``flushing`` state — the buffer never bounces back to ``ready``. Phase 1
(deterministic) always succeeds and always sets ``state=closed``; the
per-attempt LLM retry rewrites a closed-but-deterministic note in place.

This module is self-contained: no LLM code, no rendering. It owns
storage, locking, state, and replay only. Higher layers (heartbeat,
flush worker, reaper) consume :func:`Buffer.replay` and drive
transitions.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Literal

from lore_core.io import atomic_write_text
from lore_core.lockfile import flocked


__all__ = [
    "Buffer",
    "BufferState",
    "FlushRequest",
    "OwnerInfo",
    "ReplayedBuffer",
    "Sidecar",
    "buffers_dir",
    "done_dir",
    "BufferTransitionError",
]


SCHEMA_VERSION = 1


BufferState = Literal["accumulating", "ready", "flushing", "closed"]


# state-machine: which transitions are legal. Each key is the *from* state,
# values are the allowed *to* states. ``accumulating -> accumulating`` and
# ``flushing -> flushing`` are no-ops permitted for idempotent retry callers.
_LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    "accumulating": frozenset({"accumulating", "ready"}),
    "ready": frozenset({"ready", "flushing"}),
    "flushing": frozenset({"flushing", "closed"}),
    "closed": frozenset({"closed"}),
}


class BufferTransitionError(RuntimeError):
    """Raised when a CAS transition is not in :data:`_LEGAL_TRANSITIONS`."""


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def buffers_dir(lore_root: Path) -> Path:
    """Return ``<lore_root>/.lore/buffers/`` (created on demand)."""
    p = lore_root / ".lore" / "buffers"
    p.mkdir(parents=True, exist_ok=True)
    return p


def done_dir(lore_root: Path) -> Path:
    """Return ``<lore_root>/.lore/buffers/_done/`` (created on demand)."""
    p = buffers_dir(lore_root) / "_done"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _stem_for(transcript_id: str, local_date: str) -> str:
    """Build a buffer filename stem ``<transcript_id>__<YYYYMMDD>``.

    ``local_date`` must already be ``YYYY-MM-DD`` — we strip dashes for
    the on-disk form so the double-underscore separator stays unambiguous
    against UUIDs that contain hyphens. There is one buffer per
    ``(transcript_id, local_date)``: the session accumulates into a single
    buffer that yields a single note.
    """
    if not transcript_id or "__" in transcript_id:
        raise ValueError(f"invalid transcript_id: {transcript_id!r}")
    compact = local_date.replace("-", "")
    if len(compact) != 8 or not compact.isdigit():
        raise ValueError(f"local_date must be YYYY-MM-DD, got {local_date!r}")
    return f"{transcript_id}__{compact}"


def _now_iso() -> str:
    """ISO-8601 UTC with ``Z`` suffix (matches run_log / lockfile style)."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Sidecar dataclasses
# ---------------------------------------------------------------------------


@dataclass
class OwnerInfo:
    pid: int = 0
    start_ts: float = 0.0          # `/proc/<pid>/stat` field 22 (clock ticks since boot) on Linux
    run_id: str = ""
    host: str = ""
    claude_session_id: str = ""


@dataclass
class FlushRequest:
    # A request always means the same thing: close the session — segment,
    # extract, render, seal. Mid-session triggers never stamp one.
    trigger: str = ""              # session-end | reaper
    requested_at: str = ""         # ISO-Z UTC
    by_pid: int = 0


@dataclass
class Counters:
    turn_count: int = 0
    prompt_chars: int = 0
    files_touched_count: int = 0     # union of edits + reads (legacy)
    files_modified_count: int = 0    # edits only (canonical for narrative gating)
    files_read_count: int = 0        # reads only (provenance for interview / code-tour notes)


@dataclass
class LastSeen:
    content_hash: str = ""
    index_hint: int = -1


@dataclass
class Sidecar:
    """The full state-sidecar payload, serialised to ``<stem>.state.json``."""

    schema_version: int = SCHEMA_VERSION
    transcript_id: str = ""
    local_date: str = ""           # YYYY-MM-DD
    integration: str = ""
    wiki: str = ""
    scope: str = ""
    cwd: str = ""
    handle: str = ""
    owner: OwnerInfo = field(default_factory=OwnerInfo)
    state: BufferState = "accumulating"
    created_at: str = ""
    last_appended_at: str = ""
    last_heartbeat: str = ""
    counters: Counters = field(default_factory=Counters)
    last_seen: LastSeen = field(default_factory=LastSeen)
    stub_path: str = ""            # absolute path to canonical session note (set on first stub write)
    flush_attempts: int = 0
    last_error: str | None = None
    flush_requested: FlushRequest | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Drop None / empty optional sub-objects to keep the on-disk file lean
        # and round-trippable. ``flush_requested=None`` becomes absent.
        if self.flush_requested is None:
            d.pop("flush_requested", None)
        return d

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Sidecar":
        owner_raw = raw.get("owner") or {}
        counters_raw = raw.get("counters") or {}
        last_seen_raw = raw.get("last_seen") or {}
        flush_req_raw = raw.get("flush_requested")
        return cls(
            schema_version=int(raw.get("schema_version", SCHEMA_VERSION)),
            transcript_id=str(raw.get("transcript_id", "")),
            local_date=str(raw.get("local_date", "")),
            integration=str(raw.get("integration", "")),
            wiki=str(raw.get("wiki", "")),
            scope=str(raw.get("scope", "")),
            cwd=str(raw.get("cwd", "")),
            handle=str(raw.get("handle", "")),
            owner=OwnerInfo(**{k: owner_raw.get(k, getattr(OwnerInfo(), k))
                               for k in OwnerInfo.__dataclass_fields__}),
            state=raw.get("state", "accumulating"),  # type: ignore[arg-type]
            created_at=str(raw.get("created_at", "")),
            last_appended_at=str(raw.get("last_appended_at", "")),
            last_heartbeat=str(raw.get("last_heartbeat", "")),
            counters=Counters(**{k: counters_raw.get(k, getattr(Counters(), k))
                                 for k in Counters.__dataclass_fields__}),
            last_seen=LastSeen(**{k: last_seen_raw.get(k, getattr(LastSeen(), k))
                                  for k in LastSeen.__dataclass_fields__}),
            stub_path=str(raw.get("stub_path", "")),
            flush_attempts=int(raw.get("flush_attempts", 0)),
            last_error=raw.get("last_error"),
            flush_requested=(
                FlushRequest(**{k: flush_req_raw.get(k, getattr(FlushRequest(), k))
                                for k in FlushRequest.__dataclass_fields__})
                if isinstance(flush_req_raw, dict) else None
            ),
        )


# ---------------------------------------------------------------------------
# Replayed view
# ---------------------------------------------------------------------------


@dataclass
class SlicePointer:
    """One contiguous chunk's reach into the transcript."""

    from_hash: str
    to_hash: str
    from_index: int
    to_index: int


@dataclass
class ReplayedBuffer:
    """In-memory fold of a buffer's JSONL log.

    All accumulators are *deterministic only*: file paths, plan / project
    refs, commit SHAs, pre-rendered Activity bullet lines. Narrative
    bullets (worked_on / decisions / loose_ends) and the ``## Summary``
    paragraph live in Phase 2's LLM output, never in the buffer.
    """

    files_touched: list[str] = field(default_factory=list)   # union (legacy)
    files_modified: list[str] = field(default_factory=list)  # edits only
    files_read: list[str] = field(default_factory=list)      # reads only
    projects: list[str] = field(default_factory=list)
    commit_shas: list[str] = field(default_factory=list)
    activity_commits: list[str] = field(default_factory=list)
    activity_issues_opened: list[str] = field(default_factory=list)
    activity_issues_closed: list[str] = field(default_factory=list)
    slices: list[SlicePointer] = field(default_factory=list)
    turn_count: int = 0
    prompt_chars: int = 0
    cap_tripped_at: str | None = None


def _dedup_extend(target: list[str], incoming: Iterable[Any]) -> None:
    """Append items not already present (first-seen order, str-only)."""
    seen = set(target)
    for item in incoming:
        if not isinstance(item, str) or not item or item in seen:
            continue
        seen.add(item)
        target.append(item)


# ---------------------------------------------------------------------------
# Buffer
# ---------------------------------------------------------------------------


class Buffer:
    """Single-buffer handle: state sidecar, append log, per-buffer flock.

    Construct via :meth:`Buffer.open`. All mutations go through
    :meth:`with_lock`; readers (``replay``, ``read_sidecar``) do NOT take
    the flock — they snapshot whatever is on disk. The reaper / handover
    paths read sidecars without locking; mutators serialise on the flock.
    """

    def __init__(self, lore_root: Path, stem: str) -> None:
        self._lore_root = lore_root
        self._stem = stem
        base = buffers_dir(lore_root)
        self._sidecar_path = base / f"{stem}.state.json"
        self._log_path = base / f"{stem}.jsonl"
        self._lock_path = base / f"{stem}.lock"

    # ----- properties --------------------------------------------------------

    @property
    def stem(self) -> str:
        return self._stem

    @property
    def sidecar_path(self) -> Path:
        return self._sidecar_path

    @property
    def log_path(self) -> Path:
        return self._log_path

    @property
    def lock_path(self) -> Path:
        return self._lock_path

    @property
    def lore_root(self) -> Path:
        return self._lore_root

    # ----- lifecycle ---------------------------------------------------------

    @classmethod
    def open(
        cls,
        lore_root: Path,
        *,
        transcript_id: str,
        local_date: str,
    ) -> "Buffer":
        """Return a Buffer for ``(transcript_id, local_date)``.

        Does NOT create the sidecar — call :meth:`init_sidecar` while
        holding the lock if the buffer is fresh. ``open`` is just an
        addressing primitive.
        """
        stem = _stem_for(transcript_id, local_date)
        return cls(lore_root, stem)

    @classmethod
    def from_sidecar_path(cls, path: Path) -> "Buffer":
        """Construct from an existing ``<stem>.state.json`` path."""
        stem = path.name
        if stem.endswith(".state.json"):
            stem = stem[: -len(".state.json")]
        else:
            raise ValueError(f"not a sidecar path: {path}")
        # Walk up to ``.lore/buffers/`` then to the lore_root.
        buffers = path.parent
        if buffers.name != "buffers" or buffers.parent.name != ".lore":
            raise ValueError(f"sidecar not in expected layout: {path}")
        lore_root = buffers.parent.parent
        return cls(lore_root, stem)

    def exists(self) -> bool:
        return self._sidecar_path.exists()

    def init_sidecar(self, sidecar: Sidecar) -> None:
        """Write the initial sidecar. Caller must hold :meth:`with_lock`.

        ``created_at``, ``last_appended_at``, ``last_heartbeat``, and
        ``state`` default to ``now`` / ``"accumulating"`` if unset.
        """
        if self._sidecar_path.exists():
            raise BufferTransitionError(
                f"buffer already initialised: {self._sidecar_path}"
            )
        now = _now_iso()
        if not sidecar.created_at:
            sidecar.created_at = now
        if not sidecar.last_appended_at:
            sidecar.last_appended_at = now
        if not sidecar.last_heartbeat:
            sidecar.last_heartbeat = now
        self._write_sidecar(sidecar)

    # ----- locking -----------------------------------------------------------

    @contextmanager
    def with_lock(self, *, blocking: bool = True) -> Iterator[bool]:
        """Acquire the per-buffer flock.

        Yields ``True`` once the kernel grants ``LOCK_EX``; yields
        ``False`` immediately when ``blocking=False`` and the lock is
        held. flocked() handles cleanup on exit (release + close).
        """
        with flocked(self._lock_path, blocking=blocking) as held:
            yield held

    # ----- sidecar I/O -------------------------------------------------------

    def read_sidecar(self) -> Sidecar | None:
        """Return the current sidecar, or ``None`` when missing/unreadable.

        Lockless: snapshots whatever is on disk. Atomic-write semantics
        (tmp+rename in ``_write_sidecar``) ensure readers never see a
        partial file.
        """
        if not self._sidecar_path.exists():
            return None
        try:
            raw = json.loads(self._sidecar_path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict):
            return None
        return Sidecar.from_dict(raw)

    def _write_sidecar(self, sidecar: Sidecar) -> None:
        atomic_write_text(
            self._sidecar_path,
            json.dumps(sidecar.to_dict(), default=str, sort_keys=False),
        )

    # ----- transitions -------------------------------------------------------

    def transition(self, new_state: BufferState, **patch: Any) -> Sidecar:
        """CAS the sidecar's state, optionally patching other fields.

        MUST be called while holding :meth:`with_lock`. Re-reads the
        sidecar from disk so the CAS is genuinely atomic w.r.t. other
        lock holders that may have advanced state since we last read.

        ``new_state`` must be reachable per :data:`_LEGAL_TRANSITIONS`
        from the current state, otherwise :class:`BufferTransitionError`
        is raised. Patch keys that don't exist on :class:`Sidecar` are
        rejected for the same reason — silent typos here cost real data.
        """
        sidecar = self.read_sidecar()
        if sidecar is None:
            raise BufferTransitionError(
                f"transition requires existing sidecar: {self._sidecar_path}"
            )
        legal = _LEGAL_TRANSITIONS.get(sidecar.state, frozenset())
        if new_state not in legal:
            raise BufferTransitionError(
                f"illegal transition {sidecar.state!r} -> {new_state!r} "
                f"(allowed: {sorted(legal)})"
            )
        sidecar.state = new_state
        for key, value in patch.items():
            if not hasattr(sidecar, key):
                raise BufferTransitionError(
                    f"unknown sidecar field in patch: {key!r}"
                )
            setattr(sidecar, key, value)
        self._write_sidecar(sidecar)
        return sidecar

    def patch(self, **patch: Any) -> Sidecar:
        """Update sidecar fields without changing state.

        Same locking contract as :meth:`transition`. Useful for bumping
        ``last_heartbeat`` / ``flush_attempts`` / ``last_error`` etc.
        """
        sidecar = self.read_sidecar()
        if sidecar is None:
            raise BufferTransitionError(
                f"patch requires existing sidecar: {self._sidecar_path}"
            )
        for key, value in patch.items():
            if not hasattr(sidecar, key):
                raise BufferTransitionError(
                    f"unknown sidecar field in patch: {key!r}"
                )
            setattr(sidecar, key, value)
        self._write_sidecar(sidecar)
        return sidecar

    # ----- event log ---------------------------------------------------------

    def append_event(self, event: dict[str, Any]) -> None:
        """Append a JSONL event line. MUST hold :meth:`with_lock`.

        ``ts`` is auto-stamped if absent. The full line is ``json.dumps``-ed
        with ``default=str`` so datetimes and Paths round-trip safely.

        Atomicity: POSIX guarantees ``write(2)`` atomicity only up to
        PIPE_BUF (4096 bytes). We rely on the per-buffer flock for
        atomicity, NOT on ``O_APPEND`` — large payloads (e.g. wide
        files_touched lists) stay coherent because no other writer can
        interleave.
        """
        if "ts" not in event:
            event = {"ts": _now_iso(), **event}
        line = json.dumps(event, default=str) + "\n"
        # O_APPEND so concurrent reads (replay during a soft race) never
        # see a truncated file.
        fd = os.open(str(self._log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)

    def replay(self) -> ReplayedBuffer:
        """Fold the JSONL log into an in-memory accumulator view.

        Lockless. Skips malformed lines (warn-by-counter not raise).
        Unknown event types are accepted and ignored — forward-compatible
        with future event additions.
        """
        rb = ReplayedBuffer()
        if not self._log_path.exists():
            return rb
        try:
            with self._log_path.open("r", encoding="utf-8") as fh:
                for raw_line in fh:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    self._fold_event(rb, event)
        except OSError:
            pass
        return rb

    @staticmethod
    def _fold_event(rb: ReplayedBuffer, event: dict[str, Any]) -> None:
        etype = event.get("type", "")
        if etype == "append":
            _dedup_extend(rb.files_touched, event.get("files_touched") or [])
            _dedup_extend(rb.files_modified, event.get("files_modified") or [])
            _dedup_extend(rb.files_read, event.get("files_read") or [])
            _dedup_extend(rb.projects, event.get("projects") or [])
            _dedup_extend(rb.commit_shas, event.get("commit_shas") or [])
            _dedup_extend(rb.activity_commits, event.get("activity_commits") or [])
            _dedup_extend(rb.activity_issues_opened, event.get("activity_issues_opened") or [])
            _dedup_extend(rb.activity_issues_closed, event.get("activity_issues_closed") or [])
            tc = event.get("turn_count_delta")
            if isinstance(tc, int) and tc > 0:
                rb.turn_count += tc
            pc = event.get("prompt_chars_delta")
            if isinstance(pc, int) and pc > 0:
                rb.prompt_chars += pc
            slc = event.get("slice")
            if isinstance(slc, dict):
                try:
                    rb.slices.append(SlicePointer(
                        from_hash=str(slc.get("from_hash", "")),
                        to_hash=str(slc.get("to_hash", "")),
                        from_index=int(slc.get("from_index", -1)),
                        to_index=int(slc.get("to_index", -1)),
                    ))
                except (TypeError, ValueError):
                    pass
        elif etype == "cap-tripped":
            ts = event.get("ts")
            if isinstance(ts, str):
                rb.cap_tripped_at = ts
        # ``heartbeat`` events carry no accumulator deltas; presence-only.

    # ----- close -------------------------------------------------------------

    def close(self) -> tuple[Path, Path] | None:
        """Move sidecar+log to ``.lore/buffers/_done/``. Returns new paths.

        MUST hold :meth:`with_lock`. Idempotent: if files are already gone
        (a parallel reaper closed first), returns ``None``.

        Refuses to silently clobber an existing ``_done/<stem>.state.json``
        (or its companion ``.jsonl``). A pre-existing archived sidecar
        with the same stem means a duplicate buffer was opened for an
        already-archived ``(transcript_id, local_date)`` pair; clobbering
        it would erase the archived state. We raise instead so the
        underlying bug surfaces loudly rather than corrupting history.
        """
        if not self._sidecar_path.exists() and not self._log_path.exists():
            return None
        target_dir = done_dir(self._lore_root)
        new_sidecar = target_dir / self._sidecar_path.name
        new_log = target_dir / self._log_path.name
        if new_sidecar.exists() or new_log.exists():
            raise BufferTransitionError(
                f"refusing to overwrite existing _done/ archive for stem "
                f"{self._stem!r}: an archived sidecar / log already exists "
                "at the target path. This usually means a duplicate buffer "
                "was opened for an already-archived (transcript_id, "
                "local_date) pair."
            )
        # Use os.replace for cross-FS atomicity within the same FS (which is
        # guaranteed since done_dir() lives inside .lore/buffers/ on the same
        # mount as the source).
        if self._sidecar_path.exists():
            os.replace(self._sidecar_path, new_sidecar)
        if self._log_path.exists():
            os.replace(self._log_path, new_log)
        return new_sidecar, new_log

    def reopen_from_done(self) -> Sidecar | None:
        """Restore this stem's archived buffer from ``_done/`` for continuation.

        Moves ``_done/<stem>.state.json`` (and ``.jsonl`` if present) back to
        the live buffers dir, then resets the sidecar to a fresh accumulation
        cycle — state ``accumulating``, counters zeroed, event log truncated,
        flush bookkeeping cleared — while preserving the note pointer
        (``stub_path``), session identity, and the transcript watermark
        (``last_seen``). Returns the restored :class:`Sidecar`, or ``None``
        when no archived buffer exists for this stem. Caller must hold
        :meth:`with_lock`.

        This is the buffer half of the reopen-and-continue path: a session
        that was closed — a false liveness reap, or a genuine close followed
        by a resume — reattaches to its own buffer instead of minting a
        duplicate. The archive is *moved* (not copied) so the "one buffer per
        stem" invariant holds: a later :meth:`close` archives cleanly with no
        ``_done/`` collision. The event log is truncated because the note
        already carries every flushed chapter; only new turns accumulate now.
        """
        done = done_dir(self._lore_root)
        archived_sidecar = done / self._sidecar_path.name
        archived_log = done / self._log_path.name
        if not archived_sidecar.exists():
            return None
        os.replace(archived_sidecar, self._sidecar_path)
        if archived_log.exists():
            os.replace(archived_log, self._log_path)
        sidecar = self.read_sidecar()
        if sidecar is None:
            return None
        sidecar.state = "accumulating"
        sidecar.counters = Counters()
        sidecar.flush_attempts = 0
        sidecar.last_error = None
        sidecar.flush_requested = None
        self._write_sidecar(sidecar)
        self._log_path.write_text("")
        return sidecar


# ---------------------------------------------------------------------------
# Cross-buffer iteration (used by reaper / SessionEnd / status)
# ---------------------------------------------------------------------------


def iter_all(lore_root: Path) -> Iterator[Buffer]:
    """Yield every live (non-_done) Buffer under ``<lore_root>/.lore/buffers``.

    Reads only directory entries; does not touch sidecars. The caller
    decides which to read.
    """
    base = buffers_dir(lore_root)
    for entry in sorted(base.iterdir()):
        if entry.is_dir():
            continue  # skip _done/
        if not entry.name.endswith(".state.json"):
            continue
        try:
            yield Buffer.from_sidecar_path(entry)
        except ValueError:
            continue


def iter_for_pid(lore_root: Path, pid: int) -> Iterator[Buffer]:
    """Yield live buffers whose ``owner.pid`` matches ``pid``.

    Reads each candidate's sidecar (lockless). Used by the SessionEnd
    handover path to flush "all my buffers" — a multi-day session may
    own buffers under several local-date keys.
    """
    for buf in iter_all(lore_root):
        sidecar = buf.read_sidecar()
        if sidecar is None:
            continue
        if sidecar.owner.pid == pid:
            yield buf
