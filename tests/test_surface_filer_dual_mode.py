"""Integration tests for ``file_surface`` Phase 3 dual-mode routing.

When ``LORE_PROJECT_FOLDERS=on`` AND a ``projects/<slug>/`` folder exists
for the cluster's scope, the note lands inside that project's
``concepts/`` (or ``decisions/``, etc.) subfolder. Otherwise the note
lands at the legacy flat path.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from lore_core.surfaces import SurfaceDef, SurfacesDoc
from lore_curator.surface_filer import file_surface


_NOW = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


def _surfaces_doc() -> SurfacesDoc:
    return SurfacesDoc(
        schema_version=2,
        path=Path("<test>"),
        surfaces=[
            SurfaceDef(
                name="concept",
                description="A concept.",
                required=["type", "created", "last_reviewed", "description", "tags"],
                plural="concepts",
            ),
            SurfaceDef(
                name="decision",
                description="A decision.",
                required=["type", "created", "last_reviewed", "description", "tags"],
                plural="decisions",
            ),
        ],
    )


def test_file_surface_legacy_flat_when_toggle_off(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_PROJECT_FOLDERS", "off")
    # A project folder exists, but the toggle is off — must use flat.
    (tmp_path / "projects" / "ops-db").mkdir(parents=True)

    filed = file_surface(
        surface_name="concept",
        title="Event Sourcing Pattern",
        body="...",
        sources=["[[2026-04-28-foo]]"],
        wiki_root=tmp_path,
        surfaces_doc=_surfaces_doc(),
        extra_frontmatter={"tags": ["topic/db"]},
        now=_NOW,
        scope="ccat:ops-db",
    )
    assert filed.path == tmp_path / "concepts" / "event-sourcing-pattern.md"


def test_file_surface_routes_into_project_folder_when_toggle_on(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_PROJECT_FOLDERS", "on")
    project_dir = tmp_path / "projects" / "ops-db"
    project_dir.mkdir(parents=True)

    filed = file_surface(
        surface_name="concept",
        title="Event Sourcing Pattern",
        body="...",
        sources=["[[2026-04-28-foo]]"],
        wiki_root=tmp_path,
        surfaces_doc=_surfaces_doc(),
        extra_frontmatter={"tags": ["topic/db"]},
        now=_NOW,
        scope="ccat:ops-db",
    )
    assert filed.path == project_dir / "concepts" / "event-sourcing-pattern.md"


def test_file_surface_falls_back_to_flat_when_project_folder_missing(tmp_path, monkeypatch):
    """Toggle on, but no project folder for this scope → flat fallback."""
    monkeypatch.setenv("LORE_PROJECT_FOLDERS", "on")
    # Wiki has no projects/ subtree at all.

    filed = file_surface(
        surface_name="decision",
        title="Use Postgres",
        body="...",
        sources=["[[2026-04-28-foo]]"],
        wiki_root=tmp_path,
        surfaces_doc=_surfaces_doc(),
        extra_frontmatter={"tags": ["topic/db"]},
        now=_NOW,
        scope="ccat:nope",
    )
    assert filed.path == tmp_path / "decisions" / "use-postgres.md"


def test_file_surface_no_scope_uses_flat_path(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_PROJECT_FOLDERS", "on")
    (tmp_path / "projects" / "ops-db").mkdir(parents=True)

    filed = file_surface(
        surface_name="concept",
        title="Generic Concept",
        body="...",
        sources=["[[2026-04-28-foo]]"],
        wiki_root=tmp_path,
        surfaces_doc=_surfaces_doc(),
        extra_frontmatter={"tags": ["topic/x"]},
        now=_NOW,
        scope=None,
    )
    assert filed.path == tmp_path / "concepts" / "generic-concept.md"


def test_file_surface_keeps_existing_callers_working(tmp_path, monkeypatch):
    """Backward compat: callers that don't pass ``scope`` keep using flat path."""
    monkeypatch.setenv("LORE_PROJECT_FOLDERS", "on")
    (tmp_path / "projects" / "ops-db").mkdir(parents=True)

    filed = file_surface(
        surface_name="concept",
        title="No Scope Caller",
        body="...",
        sources=["[[2026-04-28-foo]]"],
        wiki_root=tmp_path,
        surfaces_doc=_surfaces_doc(),
        extra_frontmatter={"tags": ["topic/x"]},
        now=_NOW,
        # scope omitted → defaults to None → flat path.
    )
    assert filed.path == tmp_path / "concepts" / "no-scope-caller.md"
