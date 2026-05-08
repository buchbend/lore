"""Per-user personal-confirm sidecar — slice 6 of PRD #65.

Records "I personally confirm this note is OK to use" verdicts in a
per-user JSON file at ``wiki/<name>/_verdicts/<handle>.json``. The
asymmetric storage (frontmatter for team-wide stale markers; sidecar
for personal confirms) is load-bearing — see PRD #65: a confirm is a
*personal* "I'm OK using this," it cannot vouch for the team-wide
truth.

Schema (canonical):

.. code-block:: json

    {
      "confirmed": {
        "<note-relative-path>": "YYYY-MM-DD"
      }
    }

Atomic writes via the standard ``.tmp`` + ``os.replace`` pattern so
an interrupted write never leaves a partial file. Multi-handle
isolation is enforced by the path: writing one handle's sidecar
never touches another handle's.
"""

from __future__ import annotations

import json
import os
from datetime import date as _date
from pathlib import Path


def _sanitize_handle(handle: str) -> str:
    """Reject path-escape attempts in handle strings.

    Handles are arbitrary user input from ``_users.yml`` — a hostile
    handle like ``../../etc/passwd`` would otherwise resolve outside
    the wiki. We reject any handle that contains a path separator.
    """
    h = handle.strip()
    if not h:
        raise ValueError("handle must be non-empty")
    if "/" in h or "\\" in h or h.startswith(".") or "\x00" in h:
        raise ValueError(f"invalid handle: {handle!r}")
    return h


def _sidecar_path(wiki_path: Path, handle: str) -> Path:
    return wiki_path / "_verdicts" / f"{_sanitize_handle(handle)}.json"


def _read(wiki_path: Path, handle: str) -> dict:
    p = _sidecar_path(wiki_path, handle)
    if not p.exists():
        return {"confirmed": {}}
    try:
        data = json.loads(p.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {"confirmed": {}}
    if not isinstance(data, dict):
        return {"confirmed": {}}
    if not isinstance(data.get("confirmed"), dict):
        data["confirmed"] = {}
    return data


def _atomic_write(p: Path, data: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    os.replace(tmp, p)


def get_confirmed(
    wiki_path: Path, handle: str, note_path: str
) -> _date | None:
    """Return the most recent personal confirm for ``note_path``.

    Returns ``None`` if no confirm exists, or if the stored value is
    unparseable. The note path is wiki-relative (e.g.
    ``concepts/foo.md``).
    """
    data = _read(wiki_path, handle)
    val = data.get("confirmed", {}).get(note_path)
    if not isinstance(val, str):
        return None
    try:
        return _date.fromisoformat(val)
    except ValueError:
        return None


def set_confirmed(
    wiki_path: Path, handle: str, note_path: str, when: _date | None = None
) -> _date:
    """Record a personal confirm for ``note_path`` and return the date written."""
    when = when or _date.today()
    data = _read(wiki_path, handle)
    data.setdefault("confirmed", {})[note_path] = when.isoformat()
    _atomic_write(_sidecar_path(wiki_path, handle), data)
    return when


def clear_confirmed(wiki_path: Path, handle: str, note_path: str) -> bool:
    """Remove a personal confirm for ``note_path``. Returns whether one was removed."""
    data = _read(wiki_path, handle)
    confirmed = data.get("confirmed", {})
    if note_path not in confirmed:
        return False
    del confirmed[note_path]
    _atomic_write(_sidecar_path(wiki_path, handle), data)
    return True
