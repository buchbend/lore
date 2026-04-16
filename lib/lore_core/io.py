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
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    if not content.endswith("\n"):
        content += "\n"
    tmp.write_text(content, encoding=encoding)
    os.replace(tmp, path)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write bytes to `path` atomically via a sibling .tmp file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
