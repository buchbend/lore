"""``resolve_scope``: a thin shim over :func:`lore_core.route.resolve_route`.

Kept only for the attach and project commands, which PRD 0016 deletes.
New code calls ``resolve_route``.
"""

from __future__ import annotations

from pathlib import Path

from lore_core.route import resolve_route
from lore_core.types import Scope


def resolve_scope(cwd: Path, attachments: object = None) -> Scope | None:
    """The route at ``cwd`` as a :class:`Scope`, or ``None`` when no wiki routes it.

    ``attachments`` is ignored; ``attachments.json`` no longer routes.
    """
    route = resolve_route(cwd)
    if not route.wiki:
        return None
    return Scope(
        wiki=route.wiki,
        scope=route.scope or "",
        backend="none",
        claude_md_path=route.claude_md_path,
    )
