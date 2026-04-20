"""Scope resolver: walk up from cwd for CLAUDE.md with ## Lore block."""

from __future__ import annotations

from pathlib import Path

from lore_core.attach import read_attach
from lore_core.types import Scope


def resolve_scope(cwd: Path, *, max_depth: int = 8) -> Scope | None:
    """Walk up from `cwd` looking for CLAUDE.md with a `## Lore` block.

    Returns the nearest `Scope` (child wins over ancestor), or `None`
    if no attached CLAUDE.md is found within `max_depth` levels.
    """
    current = cwd.resolve()
    depth = 0

    while depth < max_depth:
        claude_md_path = current / "CLAUDE.md"
        block = read_attach(claude_md_path)

        if block:  # Non-empty block means ## Lore section found
            wiki = block.get("wiki")
            scope = block.get("scope")

            if wiki and scope:
                backend = block.get("backend", "none")
                return Scope(
                    wiki=wiki,
                    scope=scope,
                    backend=backend,
                    claude_md_path=claude_md_path,
                )

        # Move up one level
        parent = current.parent
        if parent == current:  # Hit filesystem root
            break
        current = parent
        depth += 1

    return None
