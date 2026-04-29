"""Markdown-file briefing sink.

Writes the briefing to a markdown file at a configured path. Simplest
sink — works for wikis that want briefings stored as notes (Obsidian,
git history, GitHub rendering). The ``YYYY-MM-DD`` substring in the
target path is replaced with today's date.

Path resolution order:

    1. URI target  (``markdown:/tmp/foo.md``) — wins, lets debug
       overrides keep working
    2. yaml field  (``markdown.path:`` in ``.lore-briefing.yml``,
       or flat top-level ``path:`` with a deprecation warning)
    3. error
"""

from __future__ import annotations

import os
import warnings
from datetime import date
from pathlib import Path
from typing import Any

from lore_core.briefing.sinks import register
from lore_core.io import atomic_write_text

_FLAT_DEPRECATION_WARNED = False


def _resolve_path(target: str, config: dict[str, Any] | None) -> str:
    """Resolve the output path from URI target → nested yaml → flat yaml."""
    global _FLAT_DEPRECATION_WARNED
    if target:
        return target
    if config:
        nested = config.get("markdown") or {}
        if isinstance(nested, dict):
            v = nested.get("path")
            if isinstance(v, str) and v.strip():
                return v.strip()
        v = config.get("path")
        if isinstance(v, str) and v.strip():
            if not _FLAT_DEPRECATION_WARNED:
                warnings.warn(
                    "markdown sink: flat top-level `path:` in "
                    ".lore-briefing.yml is deprecated; nest under "
                    "`markdown:` instead.",
                    DeprecationWarning,
                    stacklevel=3,
                )
                _FLAT_DEPRECATION_WARNED = True
            return v.strip()
    return ""


def _send(target: str, text: str, config: dict[str, Any] | None) -> None:
    """Atomic-write ``text`` to the path (URI target wins, yaml fallback)."""
    raw = _resolve_path(target, config)
    if not raw:
        raise ValueError(
            "markdown sink requires a target path: pass `markdown:<path>` "
            "or set `markdown.path:` in .lore-briefing.yml."
        )
    path = Path(os.path.expanduser(raw))
    if "YYYY-MM-DD" in str(path):
        path = Path(str(path).replace("YYYY-MM-DD", date.today().isoformat()))
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, text)


register("markdown", _send)
