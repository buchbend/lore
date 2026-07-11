"""Persist hook crash tracebacks to disk.

Hook failures surface to the user as a friendly banner via `_emit`, but
the actual traceback is what the maintainer needs to fix the bug. This
module writes one file per crash under ``$LORE_CACHE/crashes/`` so:

  * `lore doctor` can tell the user "N crashes in last 7 days" + path
  * the user can attach the file to a GitHub issue
  * the agent itself can read it when the user asks "what crashed?"

Best-effort by design — if the cache dir isn't writable, we silently
return ``None`` rather than triggering a second crash inside the
crash handler. Callers must tolerate ``None``.
"""

from __future__ import annotations

import os
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path


def _crash_dir() -> Path:
    base = os.environ.get("LORE_CACHE") or str(Path.home() / ".cache" / "lore")
    return Path(base) / "crashes"


def _is_dev_invocation() -> bool:
    """True when this process is a developer-side invocation that shouldn't
    pollute the real crash log.

    Two cases produce noise that masks real production hook crashes from
    `lore doctor` and the maintainer's signal:

    * **pytest test runs** — tests intentionally simulate hook failures
      to verify the crash-handling pipeline (see ``test_directive_template.py``).
      Those simulated tracebacks land in the user's real ``~/.cache/lore/crashes/``
      and inflate the doctor count.
    * **manual --dry-run debugging** — when a user runs
      ``lore curator run --dry-run`` to investigate a backend, any
      transient failure (missing dep, bad config) writes a crash entry
      that's not actually a hook bug.

    Detection is conservative — only filter when the signal is unambiguous.
    """
    argv0 = (sys.argv[0] if sys.argv else "") or ""
    if "pytest" in argv0:
        return True
    if "--dry-run" in sys.argv:
        return True
    return False


def write_crash(event: str, exc: BaseException) -> Path | None:
    """Write a timestamped traceback file. Returns the path, or None on
    secondary failure (cache unwritable, disk full, etc.).

    ``event`` is a short label (``SessionStart``, ``main``, …). It is
    sanitized for filesystem safety. Returns ``None`` (silently skipped)
    when this is a developer-side invocation — see :func:`_is_dev_invocation`.
    """
    if _is_dev_invocation():
        return None
    safe_event = "".join(c if c.isalnum() or c in "-_" else "_" for c in event) or "unknown"
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = _crash_dir() / f"{ts}-{safe_event}.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = [
            f"event: {event}",
            f"timestamp: {ts}",
            f"argv: {sys.argv!r}",
            f"cwd: {os.getcwd()}",
            f"exception: {type(exc).__name__}: {exc}",
            "",
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        ]
        path.write_text("\n".join(body))
    except OSError:
        return None
    return path


def recent_crashes(within_days: int = 7) -> list[Path]:
    """Return crash log paths newer than ``within_days``, newest first.

    Empty list when the directory doesn't exist or is unreadable —
    callers treat absence as "no crashes."
    """
    cdir = _crash_dir()
    if not cdir.exists():
        return []
    cutoff = datetime.now(UTC).timestamp() - within_days * 86400
    out: list[tuple[float, Path]] = []
    try:
        for p in cdir.iterdir():
            if not p.is_file() or not p.name.endswith(".log"):
                continue
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            if mtime >= cutoff:
                out.append((mtime, p))
    except OSError:
        return []
    out.sort(key=lambda t: t[0], reverse=True)
    return [p for _, p in out]


def purge_old_crashes(within_days: int, *, lore_root: Path | None = None) -> tuple[int, int]:
    """Delete crash logs older than ``within_days``. Returns (deleted, failed).

    Crash logs live under the global ``$LORE_CACHE``, not a specific
    ``lore_root`` — so a caller without a resolved root still gets the
    deletion (pure best-effort, matching :func:`write_crash`'s degrade
    contract) but no spine visibility, since there's nowhere to log it.
    With ``lore_root``, every deletion/failure is a ``source="janitor"``
    spine event (issue #190) — crash logs no longer accumulate silently.
    """
    cdir = _crash_dir()
    if not cdir.exists():
        return 0, 0
    cutoff = datetime.now(UTC).timestamp() - within_days * 86400
    writer = None
    if lore_root is not None:
        from lore_core.spine import SpineWriter

        writer = SpineWriter(lore_root)

    deleted = 0
    failed = 0
    try:
        candidates = [p for p in cdir.iterdir() if p.is_file() and p.name.endswith(".log")]
    except OSError:
        return 0, 0
    for p in candidates:
        try:
            mtime = p.stat().st_mtime
            size = p.stat().st_size
        except OSError:
            continue
        if mtime >= cutoff:
            continue
        try:
            p.unlink()
            deleted += 1
            if writer is not None:
                writer.emit(
                    source="janitor",
                    event="retention-delete",
                    data={"family": "crash-log", "path": p.name, "bytes": size},
                )
        except OSError as exc:
            failed += 1
            if writer is not None:
                writer.emit(
                    source="janitor",
                    event="retention-delete-failed",
                    level="warn",
                    data={"family": "crash-log", "path": p.name, "error": str(exc)},
                )
    return deleted, failed
