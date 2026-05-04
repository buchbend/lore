"""Path resolver for project-folder-aware writes.

Phase 3 of the projects-as-canonical rollout introduces a dual-mode
schema where abstracted surfaces (concepts, decisions, threads) can
live under their project's folder at ``projects/<slug>/<surface-dir>/``
instead of the flat top-level ``<surface-dir>/``.

The toggle ``LORE_PROJECT_FOLDERS=on`` opts into the new layout. When
off (default), all writers use the legacy flat paths so existing
vaults stay untouched until the migration session runs (Phase 9).

Routing rule:
- A scope's last colon-separated segment is the candidate project slug.
- If ``<wiki_root>/projects/<slug>/`` exists as a directory, it is the
  resolved project folder.
- Otherwise, callers fall through to the legacy flat path.

Empty/None scopes always fall through.
"""

from __future__ import annotations

import os
from pathlib import Path


_TOGGLE_ENV = "LORE_PROJECT_FOLDERS"
_TRUTHY = {"on", "1", "true", "yes"}
_FALSY = {"off", "0", "false", "no"}


def project_folders_enabled() -> bool:
    """Return True when the ``LORE_PROJECT_FOLDERS`` toggle is on.

    Default (post step-9 flip): on. Pass ``LORE_PROJECT_FOLDERS=off`` to
    revert to legacy flat paths (emergency-rollback escape hatch).
    Truthy values: ``on``, ``1``, ``true``, ``yes`` (case-insensitive).
    """
    val = os.environ.get(_TOGGLE_ENV, "").lower()
    if val in _FALSY:
        return False
    if val in _TRUTHY:
        return True
    return True


def project_slug_for_scope(scope: str | None) -> str | None:
    """Return the candidate project slug for ``scope``, or None.

    The slug is the last colon-separated segment of the scope chain.
    ``ccat:data-center:ops-db`` → ``ops-db``. Empty/None scope returns
    None — the caller should fall through to the flat path.
    """
    if not scope:
        return None
    slug = scope.rsplit(":", 1)[-1]
    return slug or None


def project_dir_for_scope(wiki_root: Path, scope: str | None) -> Path | None:
    """Return the project folder path for ``scope``, or None if unmapped.

    Returns None when:
    - the toggle is off,
    - the scope is empty/None,
    - no ``projects/<slug>/`` folder exists in the wiki.

    Callers receiving None should fall through to the legacy flat path.
    """
    if not project_folders_enabled():
        return None
    slug = project_slug_for_scope(scope)
    if slug is None:
        return None
    candidate = wiki_root / "projects" / slug
    if candidate.is_dir():
        return candidate
    return None


def resolve_surface_dir(
    wiki_root: Path,
    surface_subdir: str,
    *,
    scope: str | None,
) -> Path:
    """Resolve the directory a surface note should be filed into.

    With ``LORE_PROJECT_FOLDERS=on`` and a matching project folder:
        ``<wiki_root>/projects/<slug>/<surface_subdir>``
    Otherwise (legacy flat path):
        ``<wiki_root>/<surface_subdir>``

    Caller is responsible for ``mkdir(parents=True, exist_ok=True)``.
    """
    project_dir = project_dir_for_scope(wiki_root, scope)
    if project_dir is not None:
        return project_dir / surface_subdir
    return wiki_root / surface_subdir
