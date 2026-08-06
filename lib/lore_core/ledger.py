"""Transcript sidecar ledger — one entry per transcript lore has seen.

The entry records identity (``integration``, ``transcript_id``), where the
transcript lives (``path``, ``directory``), when it last grew
(``last_mtime``), whether its cwd is gone (``orphan``), and what the
session worked on (``linkage``). Transcript sync, the drill tool and the
last-active-day recap read those fields.

The digest watermarks the curator pipeline wrote are gone with the
pipeline (issues #361, #377). Sync decides what to copy by comparing
filesystem modification times, so no ledger field carries a sync
guarantee.

All writes go through ``lore_core.io.atomic_write_text`` so readers never
see a partial file.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from lore_core.io import atomic_write_text
from lore_core.spine import SpineWriter


@dataclass
class TranscriptLedgerEntry:
    integration: str
    transcript_id: str
    path: Path
    directory: Path
    last_mtime: datetime
    orphan: bool = False  # cwd permanently gone; excluded from sync and recap
    total_turns: int = 0  # turns observed at last sync
    #: Where this session worked and what it produced — the personal
    #: layer's linkage store. Keys: ``repo``, ``branch`` (str), ``prs``,
    #: ``issues`` (list[int]), ``commits``, ``files`` (list[str]).
    #: Derived and rebuildable; written by capture with no LLM call.
    #: Kept a plain dict rather than a dataclass so an entry written by a
    #: future Lore with extra keys still round-trips through an older one.
    linkage: dict = field(default_factory=dict)


class TranscriptLedger:
    """Sidecar ledger at <lore_root>/.lore/transcript-ledger.json.

    The on-disk JSON is cached within a single ``TranscriptLedger``
    instance, keyed on the file's mtime. The hot-path capture hook
    issues 5+ reads against the ledger; re-parsing a 180KB+ file per
    call dominated the previous budget. External writers (other
    processes) are picked up on the next mtime change.
    """

    def __init__(self, lore_root: Path) -> None:
        self._lore_root = lore_root
        self._path = lore_root / ".lore" / "transcript-ledger.json"
        self._cache: dict[str, dict] | None = None
        self._cache_mtime: float | None = None

    def _load(self) -> dict[str, dict]:
        """Return the raw JSON dict (key → raw entry dict). Empty if absent.

        Cached on the instance; invalidates when the file mtime changes
        (another process wrote) or when this instance writes via
        :meth:`_write_raw`.
        """
        try:
            current_mtime = self._path.stat().st_mtime
        except FileNotFoundError:
            return {}
        except OSError:
            return {}
        if self._cache is not None and self._cache_mtime == current_mtime:
            return self._cache
        try:
            raw = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        self._cache = raw
        self._cache_mtime = current_mtime
        return raw

    def _write_raw(self, raw: dict[str, dict]) -> None:
        """Atomic-write the ledger and refresh the in-instance cache."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self._path, json.dumps(raw, indent=2))
        try:
            self._cache_mtime = self._path.stat().st_mtime
            self._cache = raw
        except OSError:
            self._cache = None
            self._cache_mtime = None

    @staticmethod
    def _key(integration: str, transcript_id: str) -> str:
        return f"{integration}::{transcript_id}"

    @staticmethod
    def _entry_to_raw(e: TranscriptLedgerEntry) -> dict:
        """Convert to JSON-safe dict (datetime → ISO8601, Path → str)."""
        return {
            "integration": e.integration,
            "transcript_id": e.transcript_id,
            "path": str(e.path),
            "directory": str(e.directory),
            "last_mtime": e.last_mtime.isoformat(),
            "orphan": e.orphan,
            "total_turns": e.total_turns,
            "linkage": e.linkage,
        }

    @staticmethod
    def _entry_from_raw(raw: dict) -> TranscriptLedgerEntry:
        """Inverse of _entry_to_raw.

        Keys an older Lore wrote and this one no longer carries are
        dropped on the next write of that entry — the reader ignores
        them rather than failing on a ledger it did not author.

        One-release back-compat: ledgers written by Lore ≤ 0.10.3 use
        the ``"host"`` JSON key. Read either; we always write
        ``"integration"`` going forward. The fallback can drop in 0.11.0.
        """
        return TranscriptLedgerEntry(
            integration=raw.get("integration") or raw["host"],
            transcript_id=raw["transcript_id"],
            path=Path(raw["path"]),
            directory=Path(raw["directory"]),
            last_mtime=datetime.fromisoformat(raw["last_mtime"]),
            orphan=raw.get("orphan", False),
            total_turns=raw.get("total_turns", 0),
            linkage=raw.get("linkage") or {},
        )

    def all_entries(self) -> list[TranscriptLedgerEntry]:
        """Return every ledger entry."""
        raw = self._load()
        return [self._entry_from_raw(v) for v in raw.values()]

    def get(self, integration: str, transcript_id: str) -> TranscriptLedgerEntry | None:
        raw = self._load()
        key = self._key(integration, transcript_id)
        if key not in raw:
            return None
        return self._entry_from_raw(raw[key])

    def upsert(self, entry: TranscriptLedgerEntry) -> None:
        """Write the entry; atomic replace of the ledger file."""
        raw = self._load()
        key = self._key(entry.integration, entry.transcript_id)
        raw[key] = self._entry_to_raw(entry)
        self._write_raw(raw)

    def bulk_upsert(self, entries: list[TranscriptLedgerEntry]) -> None:
        """Upsert multiple entries with a single atomic write.

        The hot-path capture hook collects every discovered transcript
        into one call, avoiding an atomic-write-per-entry storm when
        seeding a fresh vault or a previously-unseen cwd.
        """
        if not entries:
            return
        raw = self._load()
        for entry in entries:
            raw[self._key(entry.integration, entry.transcript_id)] = self._entry_to_raw(entry)
        self._write_raw(raw)

    def mark_orphan(self, integration: str, transcript_id: str) -> None:
        """Flag an entry as permanently retired: its cwd is gone or unattached.

        Transcript sync skips an orphan entry and the last-active-day
        recap excludes it. Raises ``KeyError`` if the entry is missing.
        """
        raw = self._load()
        key = self._key(integration, transcript_id)
        if key not in raw:
            raise KeyError(f"No ledger entry for {key!r}")
        entry = self._entry_from_raw(raw[key])
        entry.orphan = True
        raw[key] = self._entry_to_raw(entry)
        self._write_raw(raw)


#: A query token routes to the ledger when it looks like a path — it
#: carries a directory separator, or an extension of two-plus letters.
#: Two-plus keeps prose abbreviations ("e.g.", "i.e.") from costing a
#: ledger parse and a spine event on an ordinary topical query. Numeric
#: refs are classified separately by ``lore_core.linkage.classify_refs``.
_PATHISH_RE = re.compile(r"[\w./~-]*(?:/[\w.-]+|\.[A-Za-z]{2,5})\b")


def find_sessions(
    lore_root: Path,
    query: str,
    *,
    limit: int = 5,
) -> list[dict]:
    """Return transcript pointers for the sessions that touched ``query``.

    Routes on what the query names: an issue or PR number, or a file
    path. Anything else is not a ledger query and returns ``[]`` without
    touching the ledger — the caller's own search still answers it.

    Newest first, capped at ``limit``. Emits one read-side spine event
    per routed query so "who is actually drilling the archive?" is
    answerable from the same file as every other counter.

    Owner-local by contract (ADR 0009): the pointers are paths on this
    machine and never leave it.
    """
    from lore_core.linkage import classify_refs

    issues, prs, epics = classify_refs(query)
    refs = issues | prs | epics
    paths = {m.group(0) for m in _PATHISH_RE.finditer(query) if m.group(0)}
    if not refs and not paths:
        return []

    matched = [
        e
        for e in TranscriptLedger(lore_root).all_entries()
        if _entry_matches(e, refs, paths)
    ]
    matched.sort(key=lambda e: e.last_mtime, reverse=True)
    hits = [
        {
            "transcript_id": e.transcript_id,
            "integration": e.integration,
            "path": str(e.path),
            "directory": str(e.directory),
            "last_active": e.last_mtime.isoformat(),
            "repo": e.linkage.get("repo", ""),
            "branch": e.linkage.get("branch", ""),
            "issues": e.linkage.get("issues", []),
            "prs": e.linkage.get("prs", []),
        }
        for e in matched[:limit]
    ]

    SpineWriter(lore_root).emit(
        source="mcp",
        event="ledger-query",
        data={
            "refs": sorted(refs),
            "paths": sorted(paths),
            "hits": len(hits),
            "matched": len(matched),
        },
    )
    return hits


def _entry_matches(entry: TranscriptLedgerEntry, refs: set[int], paths: set[str]) -> bool:
    """True when the entry's linkage names any queried ref or file.

    File comparison is suffix-symmetric so a bare ``ledger.py`` finds a
    stored ``lib/lore_core/ledger.py``, and a caller pasting an absolute
    path finds the repo-relative one capture stored.
    """
    linkage = entry.linkage
    if refs and refs & {
        int(n) for n in (linkage.get("issues") or []) + (linkage.get("prs") or [])
    }:
        return True
    for stored in linkage.get("files") or []:
        for token in paths:
            if stored.endswith(token) or token.endswith(stored):
                return True
    return False
