"""Handover notes: session state that survives ``/clear`` and compaction.

The agent writes one note per working directory with ``lore handover write``.
SessionStart reads the hook payload's ``source`` and calls
:func:`session_start_block`:

- ``clear`` archives the note and injects a pointer to the archived file. A
  later clear in the same directory does not replay a stale handover.
- ``compact`` injects a pointer to the note and keeps it. One piece of work
  can cross several compactions.
- ``startup`` and ``resume`` offer the note in one line. An old note must not
  take over a fresh session.

The pointer holds the path and no note text, so a note has no size limit
(issue #575).

Home: ``$LORE_CACHE/handovers/<dirname>-<hash>.md``, keyed by the resolved
working directory. The cache works outside git repos, and each worktree has
its own directory, so parallel sessions never share a note.

Standard library only.
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path


def _cache_root() -> Path:
    return Path(os.environ.get("LORE_CACHE") or Path.home() / ".cache" / "lore")


def handover_path(cwd: Path) -> Path:
    resolved = Path(cwd).resolve()
    digest = hashlib.sha256(str(resolved).encode()).hexdigest()[:12]
    return _cache_root() / "handovers" / f"{resolved.name or 'root'}-{digest}.md"


def write(cwd: Path, text: str) -> Path:
    if not text.strip():
        raise ValueError("handover note is empty")
    path = handover_path(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return path


def read(cwd: Path) -> str | None:
    path = handover_path(cwd)
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def _written_at(path: Path) -> str:
    ts = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    return ts.strftime("%Y-%m-%d %H:%M UTC")


def archive(cwd: Path) -> Path:
    """Move the note aside. Returns the new path, or the old one without a note."""
    path = handover_path(cwd)
    if not path.is_file():
        return path
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    return path.replace(path.with_name(f"{path.stem}.{stamp}.done.md"))


def session_start_block(cwd: Path, source: str | None, *, probe: bool = False) -> str | None:
    """The SessionStart text for *source*, or None when no note exists.

    Archives the note after a ``clear`` unless *probe* is set.
    """
    path = handover_path(cwd)
    if not path.is_file():
        return None
    written = _written_at(path)
    if source in ("clear", "compact"):
        if source == "clear" and not probe:
            path = archive(cwd)
        return (
            f"## Handover (written {written})\n\n"
            f"The previous session left a handover note at `{path}`. "
            "Read the whole file before any other action. Then continue from it; "
            "confirm the next ask with the user before acting."
        )
    return f"lore: handover from {written} — run `lore handover show` to load it."


def _gist(text: str, limit: int = 80) -> str:
    """The first line under ``## Goal``, else the first prose line, clipped."""
    lines = [ln.strip() for ln in text.splitlines()]
    try:
        start = next(i for i, ln in enumerate(lines) if ln.lower() == "## goal") + 1
    except StopIteration:
        start = 0
    gist = next((ln for ln in lines[start:] if ln and not ln.startswith("#")), "")
    return gist if len(gist) <= limit else gist[: limit - 1].rstrip() + "…"


def status_line(cwd: Path, source: str | None) -> str | None:
    """The user-visible banner line: whether a note was found, and its gist.

    Call before :func:`session_start_block`, which archives after a clear.
    """
    text = read(cwd)
    if text is None:
        if source in ("clear", "compact"):
            return "lore: no handover note for this directory"
        return None
    verb = "loaded" if source in ("clear", "compact") else "available"
    gist = _gist(text)
    tail = f" — {gist}" if gist else ""
    return f"lore: handover {verb} ({_written_at(handover_path(cwd))}){tail}"


def precompact_note(cwd: Path) -> str | None:
    if read(cwd) is None:
        return None
    return (
        f"lore: handover note at {handover_path(cwd)} — if the state moved on, "
        "rewrite it with `lore handover write` so the post-compaction session "
        "gets the current state."
    )
