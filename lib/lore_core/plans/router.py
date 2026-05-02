"""Plan path routing — Phase 5 dual-mode.

When ``LORE_PROJECT_FOLDERS=on`` AND a project folder exists for the
plan's scope/repo, plans co-locate inside their project's ``plans/``
subfolder with a date-prefixed slug:

    ``projects/<project-slug>/plans/YYYY-MM-DD-<plan-slug>.md``

When the toggle is on but no project folder maps, the plan still
gets a date prefix but stays at the flat ``plans/`` path:

    ``plans/YYYY-MM-DD-<plan-slug>.md``

When the toggle is off (default), legacy behaviour is preserved
exactly:

    ``plans/<plan-slug>.md``

Read scanners (``registry.list_active``) glob all three shapes so
mixed-mode vaults (some plans migrated, others not) keep working.
"""

from __future__ import annotations

import re
from datetime import date as _date
from pathlib import Path

from lore_core.projects.router import (
    project_folders_enabled,
    project_slug_for_scope,
)


_DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(.+)$")


def derive_project_slug(repo: str | None, scope: str | None) -> str | None:
    """Project-slug resolution order: scope's last segment, then repo basename."""
    slug = project_slug_for_scope(scope)
    if slug:
        return slug
    if repo:
        return repo.rsplit("/", 1)[-1] if "/" in repo else repo
    return None


def plan_target_path(
    wiki_root: Path,
    slug: str,
    today: _date,
    *,
    repo: str | None = None,
    scope: str | None = None,
) -> Path:
    """Return the on-disk path a freshly-captured plan should be written to.

    See module docstring for path-shape rules.
    """
    if not project_folders_enabled():
        # Legacy: no date prefix, flat plans/.
        return wiki_root / "plans" / f"{slug}.md"

    date_prefix = today.isoformat()
    project_slug = derive_project_slug(repo, scope)
    if project_slug:
        project_dir = wiki_root / "projects" / project_slug
        if project_dir.is_dir():
            return project_dir / "plans" / f"{date_prefix}-{slug}.md"

    # Toggle on but no matching project folder — fall back to date-
    # prefixed flat path. Migration session will move these later.
    return wiki_root / "plans" / f"{date_prefix}-{slug}.md"


def slug_from_filename(stem: str) -> str:
    """Strip a leading ``YYYY-MM-DD-`` date prefix if present.

    Returns the stem itself when no date prefix is present (legacy
    plans). ``2026-05-01-my-feature`` → ``my-feature``;
    ``my-feature`` → ``my-feature``.
    """
    m = _DATE_PREFIX_RE.match(stem)
    return m.group(2) if m else stem


def find_existing_plan_path(wiki_root: Path, slug: str) -> Path | None:
    """Return the on-disk path of an existing plan with ``slug``, or None.

    Scans both layouts (legacy flat ``plans/`` and project-folder
    ``projects/*/plans/``), accepting either filename shape:

      - bare:        ``<slug>.md``           (legacy)
      - date-prefix: ``YYYY-MM-DD-<slug>.md`` (Phase 5)

    Used by the writer's idempotence check so a re-capture on a later
    day still resolves to the original plan instead of writing a
    duplicate at a new date-prefixed path.

    When ``LORE_PROJECT_FOLDERS=off`` the search is restricted to the
    legacy flat ``plans/`` directory so the off-path is byte-for-byte
    identical to pre-rollout behaviour. Stray content under
    ``projects/<x>/plans/`` (e.g. legacy migrations) is ignored in the
    off-mode.
    """
    if project_folders_enabled():
        for p in iter_plan_paths(wiki_root):
            if slug_from_filename(p.stem) == slug:
                return p
        return None

    flat = wiki_root / "plans"
    if not flat.is_dir():
        return None
    for p in sorted(flat.glob("*.md")):
        if slug_from_filename(p.stem) == slug:
            return p
    return None


def iter_plan_paths(wiki_root: Path):
    """Yield every plan-shaped path for ``list_active`` / readers.

    Globs both layouts:
      - ``<wiki>/plans/*.md`` (legacy flat + date-prefixed flat)
      - ``<wiki>/projects/*/plans/*.md`` (project-folder layout)

    Skip-list filtering and ``type: plan`` checks are the caller's
    responsibility.
    """
    flat = wiki_root / "plans"
    if flat.is_dir():
        yield from sorted(flat.glob("*.md"))

    projects_root = wiki_root / "projects"
    if projects_root.is_dir():
        for project in sorted(projects_root.iterdir()):
            if not project.is_dir():
                continue
            project_plans = project / "plans"
            if project_plans.is_dir():
                yield from sorted(project_plans.glob("*.md"))
