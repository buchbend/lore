"""Scope resolver — registry-backed longest-prefix match.

Single path: ``resolve_scope(cwd)`` consults the host's
``attachments.json`` via :class:`AttachmentsFile`. No filesystem walk-up,
no CLAUDE.md parsing. Returns None if the cwd isn't covered.

When callers have an existing :class:`AttachmentsFile` instance, pass it
via the ``attachments`` kwarg to avoid re-loading per call (curator A
does this once per pass).
"""

from __future__ import annotations

import os
from pathlib import Path

from lore_core.state.attachments import AttachmentsFile
from lore_core.types import Scope


def resolve_scope(
    cwd: Path,
    attachments: AttachmentsFile | None = None,
) -> Scope | None:
    """Resolve ``cwd`` to a :class:`Scope`, or ``None`` if unattached.

    When ``attachments`` is omitted, loads one from ``$LORE_ROOT`` on
    demand. Returns None if ``$LORE_ROOT`` is unset or missing.
    """
    if attachments is None:
        attachments = _load_default_attachments()
        if attachments is None:
            return None

    match = attachments.longest_prefix_match(cwd)
    if match is None:
        return None
    return Scope(
        wiki=match.wiki,
        scope=match.scope,
        backend="none",
        # Synthetic sentinel so callers that walk up from this path to
        # infer $LORE_ROOT still work (see lore_cli.hooks._infer_lore_root).
        claude_md_path=match.path / "CLAUDE.md",
    )


# Backward-compat alias retained for explicit-registry callers (curator_a).
resolve_scope_via_registry = resolve_scope


def _load_default_attachments() -> AttachmentsFile | None:
    env = os.environ.get("LORE_ROOT")
    if not env:
        return None
    lore_root = Path(env)
    if not lore_root.exists():
        return None
    af = AttachmentsFile(lore_root)
    af.load()
    return af
