"""Markdown-file briefing sink.

Writes the briefing to a markdown file at a configured path. Simplest
sink — works for wikis that want briefings stored as notes (Obsidian,
git history, GitHub rendering). The ``YYYY-MM-DD`` substring in the
target path is replaced with today's date.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from lore_core.briefing.sinks import register
from lore_core.io import atomic_write_text


def _send(target: str, text: str) -> None:
    """Atomic-write ``text`` to the path encoded in ``target``."""
    if not target:
        raise ValueError("markdown sink requires a target path: 'markdown:<path>'")
    path = Path(os.path.expanduser(target))
    if "YYYY-MM-DD" in str(path):
        path = Path(str(path).replace("YYYY-MM-DD", date.today().isoformat()))
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, text)


register("markdown", _send)
