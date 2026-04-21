"""Atomic I/O helpers — never leave a partial file behind.

Readers (SessionStart, PreCompact hooks) race with writers (linter,
curator, skill output). `.tmp + os.replace` ensures readers never see
a half-written file. POSIX atomic on same filesystem.
"""

from __future__ import annotations

import os
from pathlib import Path


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Write text to `path` atomically via a sibling .tmp file.

    If content ends without a newline, one is appended.

    Fsyncs the tmp file before rename so concurrent readers on the same
    host see a committed state even if we crash. Required by Plan 5's
    Curator C team-mode coordination (code-reviewer must-fix).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    if not content.endswith("\n"):
        content += "\n"
    # Write + fsync the bytes, then atomic-rename.
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, content.encode(encoding))
        try:
            os.fsync(fd)
        except OSError:
            pass  # fsync not supported on some filesystems; rename still atomic
    finally:
        os.close(fd)
    os.replace(tmp, path)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write bytes to `path` atomically via a sibling .tmp file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
