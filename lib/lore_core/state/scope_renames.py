"""Append-only ``_scope_renames.txt`` log (Phase 8).

Tracks every scope rename ever applied to the vault so other hosts
can replay the changes via ``lore scopes reconcile``. The file lives
at ``$LORE_ROOT/_scope_renames.txt`` and is committed to whichever
wiki repo carries the canonical ``_scopes.yml`` for the renamed
scope (or stays in ``$LORE_ROOT`` for vault-only state — caller's
choice).

Format: one tab-separated line per rename, fields:

    <iso_timestamp>\t<old_scope>\t<new_scope>\t<host>

Plain text by design (matches the ``.txt`` collection convention —
not in the wikilink graph; grep-friendly). Reader splits on ``\t``.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


_LOG_FILENAME = "_scope_renames.txt"


@dataclass(frozen=True)
class RenameEvent:
    timestamp: str
    old_scope: str
    new_scope: str
    host: str


def log_path(lore_root: Path) -> Path:
    return lore_root / _LOG_FILENAME


def append_rename(
    lore_root: Path,
    old_scope: str,
    new_scope: str,
    *,
    timestamp: datetime | None = None,
    host: str | None = None,
) -> Path:
    """Append a rename event to the log. Returns the log path.

    Creates the file if missing. Single newline at end. Write is best-
    effort: OSError logged via stderr but not raised, so a rename
    succeeds even if the log can't be written.
    """
    ts = (timestamp or datetime.now(UTC)).isoformat().replace("+00:00", "Z")
    h = host or socket.gethostname()
    line = f"{ts}\t{old_scope}\t{new_scope}\t{h}\n"
    path = log_path(lore_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass
    return path


def read_log(lore_root: Path) -> list[RenameEvent]:
    """Return all rename events in append order."""
    path = log_path(lore_root)
    if not path.is_file():
        return []
    out: list[RenameEvent] = []
    try:
        for raw in path.read_text(errors="replace").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            parts = raw.split("\t")
            if len(parts) < 4:
                continue
            ts, old, new, host = parts[0], parts[1], parts[2], parts[3]
            out.append(RenameEvent(
                timestamp=ts,
                old_scope=old,
                new_scope=new,
                host=host,
            ))
    except OSError:
        return []
    return out
