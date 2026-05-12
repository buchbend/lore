"""Path resolver for project-folder-aware surface writes.

Abstracted surfaces (concepts, decisions, threads) live under their
project's folder at ``projects/<slug>/<surface-dir>/`` when a matching
``projects/<slug>/`` exists in the wiki. Otherwise writers fall through
to the legacy flat top-level ``<surface-dir>/`` path so vaults that
haven't promoted a scope into a project folder yet keep working.

Routing rule:
- A scope's last colon-separated segment is the candidate project slug.
- If ``<wiki_root>/projects/<slug>/`` exists as a directory, it is the
  resolved project folder.
- Otherwise, callers fall through to the legacy flat path.

Empty/None scopes always fall through.
"""

from __future__ import annotations

from pathlib import Path


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
    - the scope is empty/None,
    - no ``projects/<slug>/`` folder exists in the wiki.

    Callers receiving None should fall through to the legacy flat path.
    """
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

    With a matching project folder:
        ``<wiki_root>/projects/<slug>/<surface_subdir>``
    Otherwise (legacy flat path):
        ``<wiki_root>/<surface_subdir>``

    Caller is responsible for ``mkdir(parents=True, exist_ok=True)``.
    """
    project_dir = project_dir_for_scope(wiki_root, scope)
    if project_dir is not None:
        return project_dir / surface_subdir
    return wiki_root / surface_subdir
