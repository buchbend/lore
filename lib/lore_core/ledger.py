"""Sidecar ledger with content-hash watermarks.

Transcript-level and wiki-level sidecar JSON files.  Content-hash watermarks
prevent host-side edits from silently desyncing the digested offset.  All
writes go through ``lore_core.io.atomic_write_text`` so readers never see a
partial file.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from lore_core.io import atomic_write_text

if TYPE_CHECKING:
    from lore_core.types import Scope

Resolver = Callable[[Path], "Scope | None"]


@dataclass
class TranscriptLedgerEntry:
    host: str
    transcript_id: str
    path: Path
    directory: Path
    digested_hash: str | None
    digested_index_hint: int | None
    synthesised_hash: str | None
    last_mtime: datetime
    curator_a_run: datetime | None
    noteworthy: bool | None
    session_note: str | None  # wikilink, e.g. "[[2026-04-19-slug]]"
    orphan: bool = False  # cwd permanently gone; excluded from pending()


@dataclass
class WikiLedgerEntry:
    wiki: str
    last_curator_a: datetime | None = None
    last_curator_b: datetime | None = None
    last_curator_c: datetime | None = None
    last_briefing: datetime | None = None
    pending_transcripts: int = 0
    pending_tokens_est: int = 0


class TranscriptLedger:
    """Sidecar ledger at <lore_root>/.lore/transcript-ledger.json.

    Tracks per-transcript processing state with content-hash watermarks
    rather than integer offsets — host-side edits to prior turns don't
    silently desync the Kafka-style offset.
    """

    def __init__(self, lore_root: Path) -> None:
        self._lore_root = lore_root
        self._path = lore_root / ".lore" / "transcript-ledger.json"

    def _load(self) -> dict[str, dict]:
        """Return the raw JSON dict (key → raw entry dict). Empty if absent."""
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _key(host: str, transcript_id: str) -> str:
        return f"{host}::{transcript_id}"

    @staticmethod
    def _entry_to_raw(e: TranscriptLedgerEntry) -> dict:
        """Convert to JSON-safe dict (datetime → ISO8601, Path → str)."""
        return {
            "host": e.host,
            "transcript_id": e.transcript_id,
            "path": str(e.path),
            "directory": str(e.directory),
            "digested_hash": e.digested_hash,
            "digested_index_hint": e.digested_index_hint,
            "synthesised_hash": e.synthesised_hash,
            "last_mtime": e.last_mtime.isoformat(),
            "curator_a_run": e.curator_a_run.isoformat() if e.curator_a_run is not None else None,
            "noteworthy": e.noteworthy,
            "session_note": e.session_note,
            "orphan": e.orphan,
        }

    @staticmethod
    def _entry_from_raw(raw: dict) -> TranscriptLedgerEntry:
        """Inverse of _entry_to_raw."""
        curator_a_run_raw = raw.get("curator_a_run")
        return TranscriptLedgerEntry(
            host=raw["host"],
            transcript_id=raw["transcript_id"],
            path=Path(raw["path"]),
            directory=Path(raw["directory"]),
            digested_hash=raw.get("digested_hash"),
            digested_index_hint=raw.get("digested_index_hint"),
            synthesised_hash=raw.get("synthesised_hash"),
            last_mtime=datetime.fromisoformat(raw["last_mtime"]),
            curator_a_run=datetime.fromisoformat(curator_a_run_raw) if curator_a_run_raw else None,
            noteworthy=raw.get("noteworthy"),
            session_note=raw.get("session_note"),
            orphan=raw.get("orphan", False),
        )

    def get(self, host: str, transcript_id: str) -> TranscriptLedgerEntry | None:
        raw = self._load()
        key = self._key(host, transcript_id)
        if key not in raw:
            return None
        return self._entry_from_raw(raw[key])

    def upsert(self, entry: TranscriptLedgerEntry) -> None:
        """Write the entry; atomic replace of the ledger file."""
        raw = self._load()
        key = self._key(entry.host, entry.transcript_id)
        raw[key] = self._entry_to_raw(entry)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self._path, json.dumps(raw, indent=2))

    @staticmethod
    def _is_pending(entry: TranscriptLedgerEntry) -> bool:
        """Pending semantics:

        * ``orphan=True``                  → never pending (retired).
        * ``curator_a_run is None``        → pending (never scanned).
        * ``last_mtime > curator_a_run``   → pending (grew since scan).
        * otherwise                        → not pending.

        ``curator_a_run`` is the "I looked at this entry" marker. It is
        stamped on every scan outcome — noteworthy, not-noteworthy, skip
        because no new turns, skip because below wiki threshold, skip
        because orphan. Without this, entries whose wiki never reaches
        threshold would re-trip on every hook forever.
        """
        if entry.orphan:
            return False
        if entry.curator_a_run is None:
            return True
        return entry.last_mtime > entry.curator_a_run

    def pending(
        self,
        wiki: str | None = None,
        *,
        resolver: Resolver | None = None,
    ) -> list[TranscriptLedgerEntry]:
        """Entries still awaiting a curator scan.

        When ``wiki`` is given, restrict to entries whose ``directory``
        resolves to that wiki via ``resolver``. Orphan/unattached entries
        are dropped silently — use :meth:`pending_by_wiki` if you want
        the buckets surfaced.

        ``resolver`` defaults to the legacy CLAUDE.md walk-up for
        back-compat; Phase 1 callers that want the registry path pass in
        a closure bound to an ``AttachmentsFile``.
        """
        if resolver is None:
            from lore_core.scope_resolver import resolve_scope as resolver

        result: list[TranscriptLedgerEntry] = []
        resolve_cache: dict[Path, str | None] = {}
        for raw_entry in self._load().values():
            entry = self._entry_from_raw(raw_entry)
            if not self._is_pending(entry):
                continue

            if wiki is not None:
                entry_wiki = self._resolve_wiki_cached(entry.directory, resolve_cache, resolver)
                if entry_wiki != wiki:
                    continue

            result.append(entry)
        return result

    def pending_by_wiki(
        self,
        *,
        resolver: Resolver | None = None,
    ) -> dict[str, list[TranscriptLedgerEntry]]:
        """Group pending entries by resolved wiki, with special buckets.

        Buckets:
          - ``<wiki-name>``  — attached entries grouped by their wiki.
          - ``__orphan__``   — entry.directory no longer exists on disk.
          - ``__unattached__`` — directory exists but is not covered by
            any attachment (registry path) or has no ``## Lore`` block
            in its ancestor CLAUDE.md (legacy walk-up).

        Orphan-flagged entries (``entry.orphan=True``) are excluded — the
        curator has already retired them.

        ``resolver`` defaults to the legacy walk-up. Phase 1+ callers can
        pass a registry-backed resolver.
        """
        if resolver is None:
            from lore_core.scope_resolver import resolve_scope as resolver

        buckets: dict[str, list[TranscriptLedgerEntry]] = {}
        for raw_entry in self._load().values():
            entry = self._entry_from_raw(raw_entry)
            if not self._is_pending(entry):
                continue

            key = self._bucket_for(entry.directory, resolver)
            buckets.setdefault(key, []).append(entry)
        return buckets

    @staticmethod
    def _bucket_for(directory: Path, resolver) -> str:
        """Classify an entry's directory into a wiki name or special bucket."""
        if not directory.exists():
            return "__orphan__"
        scope = resolver(directory)
        if scope is None:
            return "__unattached__"
        return scope.wiki

    @classmethod
    def _resolve_wiki_cached(
        cls,
        directory: Path,
        cache: dict[Path, str | None],
        resolver,
    ) -> str | None:
        if directory in cache:
            return cache[directory]
        if not directory.exists():
            cache[directory] = None
            return None
        scope = resolver(directory)
        value = scope.wiki if scope is not None else None
        cache[directory] = value
        return value

    def stamp_scan(
        self,
        host: str,
        transcript_id: str,
        *,
        curator_a_run: datetime,
        orphan: bool = False,
    ) -> None:
        """Mark an entry as "scanned at curator_a_run" without altering its
        content-hash watermark.

        Used by the curator when it inspected an entry but did not actually
        digest its turns — e.g. the entry's wiki was below threshold, or its
        cwd no longer exists. Setting ``curator_a_run`` shuts up
        :meth:`pending` until the transcript grows past that timestamp.

        When ``orphan=True``, the entry is also flagged as permanently
        retired and excluded from all future :meth:`pending` results.

        Raises ``KeyError`` if the entry is missing.
        """
        raw = self._load()
        key = self._key(host, transcript_id)
        if key not in raw:
            raise KeyError(f"No ledger entry for {key!r}")
        entry = self._entry_from_raw(raw[key])
        entry.curator_a_run = curator_a_run
        if orphan:
            entry.orphan = True
        raw[key] = self._entry_to_raw(entry)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self._path, json.dumps(raw, indent=2))

    def advance(
        self,
        host: str,
        transcript_id: str,
        *,
        digested_hash: str,
        digested_index_hint: int,
        noteworthy: bool,
        session_note: str | None = None,
        curator_a_run: datetime | None = None,
    ) -> None:
        """Update an existing entry's digested state. Raises KeyError if absent.

        `curator_a_run` is the timestamp of the run that produced this
        advance. Required for the mtime-based re-trigger in `pending()`
        to work — without it, an entry whose transcript grows after a
        first advance is permanently invisible. Caller should pass
        `datetime.now(UTC)` (or a frozen test value).
        """
        raw = self._load()
        key = self._key(host, transcript_id)
        if key not in raw:
            raise KeyError(f"No ledger entry for {key!r}")
        entry = self._entry_from_raw(raw[key])
        entry.digested_hash = digested_hash
        entry.digested_index_hint = digested_index_hint
        entry.noteworthy = noteworthy
        entry.session_note = session_note
        if curator_a_run is not None:
            entry.curator_a_run = curator_a_run
        raw[key] = self._entry_to_raw(entry)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self._path, json.dumps(raw, indent=2))


class WikiLedger:
    """Per-wiki sidecar tracking last curator/briefing runs + pending counters.

    Path: <lore_root>/.lore/wiki-{wiki_name}-ledger.json
    """

    def __init__(self, lore_root: Path, wiki_name: str) -> None:
        self._lore_root = lore_root
        self._wiki = wiki_name
        self._path = lore_root / ".lore" / f"wiki-{wiki_name}-ledger.json"

    def read(self) -> WikiLedgerEntry:
        """Return current state; defaults if file absent."""
        if not self._path.exists():
            return WikiLedgerEntry(wiki=self._wiki)
        try:
            raw = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError):
            return WikiLedgerEntry(wiki=self._wiki)

        def _dt(val: str | None) -> datetime | None:
            return datetime.fromisoformat(val) if val else None

        return WikiLedgerEntry(
            wiki=raw.get("wiki", self._wiki),
            last_curator_a=_dt(raw.get("last_curator_a")),
            last_curator_b=_dt(raw.get("last_curator_b")),
            last_curator_c=_dt(raw.get("last_curator_c")),
            last_briefing=_dt(raw.get("last_briefing")),
            pending_transcripts=raw.get("pending_transcripts", 0),
            pending_tokens_est=raw.get("pending_tokens_est", 0),
        )

    def write(self, entry: WikiLedgerEntry) -> None:
        """Atomic write."""

        def _iso(dt: datetime | None) -> str | None:
            return dt.isoformat() if dt is not None else None

        raw = {
            "wiki": entry.wiki,
            "last_curator_a": _iso(entry.last_curator_a),
            "last_curator_b": _iso(entry.last_curator_b),
            "last_curator_c": _iso(entry.last_curator_c),
            "last_briefing": _iso(entry.last_briefing),
            "pending_transcripts": entry.pending_transcripts,
            "pending_tokens_est": entry.pending_tokens_est,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self._path, json.dumps(raw, indent=2))

    def update_last_curator(self, role: str, *, at: datetime | None = None) -> None:
        """Write last_curator_{a,b,c} for this wiki; best-effort telemetry.

        Read-modify-write: preserves other fields. On I/O failure, emits a
        warning event to hook-events.jsonl and returns — never raises past
        this call. The update is observability, not a correctness path, so
        a crashed curator must never be prevented from completing because
        the ledger disk is full.

        Raises ValueError if role is not one of {'a', 'b', 'c'} — that is
        a programmer error, not a runtime failure.
        """
        from datetime import UTC as _UTC

        if role not in ("a", "b", "c"):
            raise ValueError(f"role must be 'a', 'b', or 'c'; got {role!r}")
        ts = at if at is not None else datetime.now(_UTC)
        try:
            entry = self.read()
            setattr(entry, f"last_curator_{role}", ts)
            self.write(entry)
        except Exception as exc:
            try:
                from lore_core.hook_log import HookEventLogger
                HookEventLogger(self._lore_root).emit(
                    event="wiki-ledger",
                    outcome="warning",
                    error={
                        "type": type(exc).__name__,
                        "message": str(exc),
                        "role": role,
                        "wiki": self._wiki,
                    },
                )
            except Exception:
                pass
