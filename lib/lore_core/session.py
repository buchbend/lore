"""Session helpers — slug and route lookup.

* :func:`_resolve_attach_block` — route lookup used by the SessionStart banner
* :func:`slugify` — slug derivation
"""

from __future__ import annotations

import re
from pathlib import Path


def _resolve_attach_block(cwd: Path) -> tuple[Path, dict] | None:
    """Route-backed lookup for ``cwd`` (see :func:`lore_core.route.resolve_route`).

    Returns ``(synthetic <repo>/CLAUDE.md path, {"wiki": ..., "scope": ...})``
    when a wiki routes ``cwd``, else ``None``.
    """
    from lore_core.route import resolve_route

    route = resolve_route(cwd)
    if not route.wiki:
        return None
    return route.claude_md_path, {"wiki": route.wiki, "scope": route.scope}


_SLUG_NONWORD = re.compile(r"[^\w\s-]")
_SLUG_DASH = re.compile(r"[\s_-]+")


def slugify(text: str) -> str:
    """Kebab-case slug from arbitrary text. Caps at 60 chars."""
    s = _SLUG_NONWORD.sub("", text.lower())
    s = _SLUG_DASH.sub("-", s).strip("-")
    return s[:60]
