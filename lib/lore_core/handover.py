"""Handover notes: session state that survives ``/clear`` and compaction.

The agent writes one note per working directory with ``lore handover write``.
SessionStart reads the hook payload's ``source`` and calls
:func:`session_start_block`:

- ``clear`` injects the full note, then archives it. A later clear in the
  same directory does not replay a stale handover.
- ``compact`` injects the full note and keeps it. One piece of work can
  cross several compactions.
- ``startup`` and ``resume`` offer the note in one line. An old note must not
  take over a fresh session.

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

#: Upper bound on a note. The whole note lands in context after a clear, so
#: it has to stay a handover, not a transcript.
BUDGET_CHARS = 12_000


class HandoverTooLong(ValueError):
    """The note exceeds :data:`BUDGET_CHARS`."""


def _cache_root() -> Path:
    return Path(os.environ.get("LORE_CACHE") or Path.home() / ".cache" / "lore")


def handover_path(cwd: Path) -> Path:
    resolved = Path(cwd).resolve()
    digest = hashlib.sha256(str(resolved).encode()).hexdigest()[:12]
    return _cache_root() / "handovers" / f"{resolved.name or 'root'}-{digest}.md"


def write(cwd: Path, text: str) -> Path:
    if not text.strip():
        raise ValueError("handover note is empty")
    if len(text) > BUDGET_CHARS:
        raise HandoverTooLong(
            f"handover note is {len(text)} chars; the limit is {BUDGET_CHARS}"
        )
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


def archive(cwd: Path) -> None:
    path = handover_path(cwd)
    if path.is_file():
        stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        path.replace(path.with_name(f"{path.stem}.{stamp}.done.md"))


def session_start_block(cwd: Path, source: str | None, *, probe: bool = False) -> str | None:
    """The SessionStart text for *source*, or None when no note exists.

    Archives the note after a ``clear`` unless *probe* is set.
    """
    path = handover_path(cwd)
    text = read(cwd)
    if text is None:
        return None
    written = _written_at(path)
    if source in ("clear", "compact"):
        block = (
            f"## Handover (written {written})\n\n"
            "The previous session left this note. Continue from it; "
            "confirm the next ask with the user before acting.\n\n"
            f"{text.rstrip()}"
        )
        if source == "clear" and not probe:
            archive(cwd)
        return block
    return f"lore: handover from {written} — run `lore handover show` to load it."


def precompact_note(cwd: Path) -> str | None:
    if read(cwd) is None:
        return None
    return (
        f"lore: handover note at {handover_path(cwd)} — if the state moved on, "
        "rewrite it with `lore handover write` so the post-compaction session "
        "gets the current state."
    )
