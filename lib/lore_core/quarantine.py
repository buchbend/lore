"""Private quarantine sidecar for chapters the publish gate withheld.

When the gate withholds a composed chapter, its text is held here — one
JSON file per entry under ``<lore_root>/.lore/quarantine/`` — while a
deterministic withheld-marker takes its place in the shared note. The
sidecar lives inside the already-private ``.lore/`` operational area, so
the withheld content (which may contain the very secret that tripped the
gate) never reaches the shared wiki.

Storage is per-entry rather than one shared index: parallel sessions
withhold concurrently, and a file-per-entry layout means two writers
never race on the same file. Reads glob the directory; each write is
atomic (``.tmp`` + ``os.replace``).

A reviewer inspects and disposes of entries via the ``lore quarantine``
CLI (list / show / clear / kill).
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

__all__ = [
    "QUARANTINE_SCHEMA_VERSION",
    "QuarantineEntry",
    "quarantine_dir_for",
    "add_entry",
    "list_entries",
    "get_entry",
    "clear_entry",
    "kill_all",
]

QUARANTINE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class QuarantineEntry:
    """One withheld chapter held privately for review.

    ``composed_text`` is the full text the gate refused — it may contain
    the secret/PII that tripped the gate, which is exactly why it lives
    here and not in the shared note.
    """

    id: str
    created: str  # ISO-8601 UTC timestamp
    category: str  # the gate category that withheld it (secret/email/...)
    note_path: str  # note this chapter belonged to (wiki-relative or absolute)
    from_turn: int
    to_turn: int
    composed_text: str


def _resolve_dir(lore_root: Path | None, quarantine_dir: Path | None) -> Path:
    """Resolve the quarantine directory from either explicit path or root.

    Exactly one of ``quarantine_dir`` / ``lore_root`` is expected; the
    explicit directory wins when both are given.
    """
    if quarantine_dir is not None:
        return quarantine_dir
    if lore_root is not None:
        return quarantine_dir_for(lore_root)
    raise ValueError("quarantine: pass either lore_root or quarantine_dir")


def quarantine_dir_for(lore_root: Path) -> Path:
    """Return the quarantine directory under the private ``.lore/`` area."""
    return lore_root / ".lore" / "quarantine"


def _entry_path(directory: Path, entry_id: str) -> Path:
    return directory / f"{entry_id}.json"


def _to_entry(data: dict) -> QuarantineEntry:
    return QuarantineEntry(
        id=str(data["id"]),
        created=str(data.get("created", "")),
        category=str(data.get("category", "")),
        note_path=str(data.get("note_path", "")),
        from_turn=int(data.get("from_turn", 0)),
        to_turn=int(data.get("to_turn", 0)),
        composed_text=str(data.get("composed_text", "")),
    )


def add_entry(
    *,
    category: str,
    note_path: str,
    from_turn: int,
    to_turn: int,
    composed_text: str,
    lore_root: Path | None = None,
    quarantine_dir: Path | None = None,
) -> QuarantineEntry:
    """Store one withheld chapter and return its :class:`QuarantineEntry`."""
    directory = _resolve_dir(lore_root, quarantine_dir)
    directory.mkdir(parents=True, exist_ok=True)

    entry = QuarantineEntry(
        id=uuid.uuid4().hex[:12],
        created=datetime.now(UTC).isoformat(),
        category=category,
        note_path=note_path,
        from_turn=int(from_turn),
        to_turn=int(to_turn),
        composed_text=composed_text,
    )
    payload = {
        "schema_version": QUARANTINE_SCHEMA_VERSION,
        "id": entry.id,
        "created": entry.created,
        "category": entry.category,
        "note_path": entry.note_path,
        "from_turn": entry.from_turn,
        "to_turn": entry.to_turn,
        "composed_text": entry.composed_text,
    }
    path = _entry_path(directory, entry.id)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return entry


def list_entries(
    *,
    lore_root: Path | None = None,
    quarantine_dir: Path | None = None,
) -> list[QuarantineEntry]:
    """Return all quarantined chapters, oldest first (by created timestamp)."""
    directory = _resolve_dir(lore_root, quarantine_dir)
    if not directory.is_dir():
        return []
    entries: list[QuarantineEntry] = []
    for f in directory.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or "id" not in data:
            continue
        entries.append(_to_entry(data))
    entries.sort(key=lambda e: (e.created, e.id))
    return entries


def get_entry(
    entry_id: str,
    *,
    lore_root: Path | None = None,
    quarantine_dir: Path | None = None,
) -> QuarantineEntry | None:
    """Return the entry with ``entry_id``, or ``None`` if absent/unreadable."""
    directory = _resolve_dir(lore_root, quarantine_dir)
    path = _entry_path(directory, entry_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or "id" not in data:
        return None
    return _to_entry(data)


def clear_entry(
    entry_id: str,
    *,
    lore_root: Path | None = None,
    quarantine_dir: Path | None = None,
) -> bool:
    """Remove one entry after review. Returns whether one was removed."""
    directory = _resolve_dir(lore_root, quarantine_dir)
    path = _entry_path(directory, entry_id)
    if not path.is_file():
        return False
    path.unlink()
    return True


def kill_all(
    *,
    lore_root: Path | None = None,
    quarantine_dir: Path | None = None,
) -> int:
    """Purge every quarantined chapter. Returns the number removed."""
    directory = _resolve_dir(lore_root, quarantine_dir)
    if not directory.is_dir():
        return 0
    removed = 0
    for f in directory.glob("*.json"):
        try:
            f.unlink()
            removed += 1
        except OSError:
            continue
    return removed
