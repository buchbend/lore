"""Session-note helpers — slug and attach lookup.

Lore writes no session note automatically; the compose pipeline that once
filed one was retired. This module keeps only the pieces other modules
still need:

* :func:`_resolve_attach_block` — attach lookup used by hooks/doctor
* :func:`slugify` — slug derivation
"""

from __future__ import annotations

import re
from pathlib import Path


def _resolve_attach_block(cwd: Path) -> tuple[Path, dict] | None:
    """Registry-backed lookup for the attachment covering ``cwd``.

    Returns:
      * ``(synthetic_claude_md_path, {"wiki": ..., "scope": ..., ...})``
        for attached cwds (the block dict merges the attachment + any
        ``.lore.yml`` at the repo root, so fields like ``backend``,
        ``issues``, ``prs`` surface to callers that need them), or
      * ``None`` for unattached cwds.
    """
    from lore_core.offer import parse_lore_yml, FILENAME as LORE_YML
    from lore_core.scope_resolver import resolve_scope

    scope = resolve_scope(cwd)
    if scope is None:
        return None
    block = {"wiki": scope.wiki, "scope": scope.scope, "backend": scope.backend}

    # Attachment paths store the repo root; look for a `.lore.yml` there
    # and merge its non-routing fields (backend, wiki_source) into the
    # block dict. This keeps downstream callers
    # (`_session_start_from_lore`, etc.) working unchanged.
    repo_root = scope.claude_md_path.parent
    offer = parse_lore_yml(repo_root / LORE_YML)
    if offer is not None:
        if offer.backend:
            block["backend"] = offer.backend
        if offer.wiki_source:
            block["wiki_source"] = offer.wiki_source

    return scope.claude_md_path, block


_SLUG_NONWORD = re.compile(r"[^\w\s-]")
_SLUG_DASH = re.compile(r"[\s_-]+")


def slugify(text: str) -> str:
    """Kebab-case slug from arbitrary text. Caps at 60 chars."""
    s = _SLUG_NONWORD.sub("", text.lower())
    s = _SLUG_DASH.sub("-", s).strip("-")
    return s[:60]
