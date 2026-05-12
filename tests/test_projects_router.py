"""Tests for project-folder-aware surface routing.

The router decides whether a surface write goes to the legacy flat
path (``<wiki_root>/<surface-subdir>/<slug>.md``) or to the new
project-folder layout (``<wiki_root>/projects/<slug>/<surface-subdir>/<slug>.md``).
The decision is gated solely by the existence of a matching
``projects/<slug>/`` folder.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lore_core.projects.router import (
    project_dir_for_scope,
    project_slug_for_scope,
    resolve_surface_dir,
)


# ---------------------------------------------------------------------------
# project_slug_for_scope — last segment of colon-separated chain
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scope,expected",
    [
        ("ccat:data-center:ops-db", "ops-db"),
        ("ccat:data-center", "data-center"),
        ("ccat", "ccat"),
        ("lore", "lore"),
        ("", None),
        (None, None),
    ],
)
def test_project_slug_for_scope(scope, expected):
    assert project_slug_for_scope(scope) == expected


# ---------------------------------------------------------------------------
# project_dir_for_scope — folder existence gates the decision
# ---------------------------------------------------------------------------


def test_project_dir_returns_path_when_folder_exists(tmp_path):
    target = tmp_path / "projects" / "ops-db"
    target.mkdir(parents=True)
    assert project_dir_for_scope(tmp_path, "ccat:ops-db") == target


def test_project_dir_returns_none_when_folder_missing(tmp_path):
    # Wiki has projects/ but no ops-db inside.
    (tmp_path / "projects").mkdir()
    assert project_dir_for_scope(tmp_path, "ccat:ops-db") is None


def test_project_dir_returns_none_for_empty_scope(tmp_path):
    (tmp_path / "projects" / "ops-db").mkdir(parents=True)
    assert project_dir_for_scope(tmp_path, "") is None
    assert project_dir_for_scope(tmp_path, None) is None


# ---------------------------------------------------------------------------
# resolve_surface_dir — the unified entry point used by writers
# ---------------------------------------------------------------------------


def test_resolve_surface_dir_project_when_match(tmp_path):
    project_dir = tmp_path / "projects" / "ops-db"
    project_dir.mkdir(parents=True)
    assert resolve_surface_dir(
        tmp_path, "concepts", scope="ccat:ops-db",
    ) == project_dir / "concepts"


def test_resolve_surface_dir_falls_back_to_flat_when_no_project_folder(tmp_path):
    """No ``projects/<slug>/`` for this scope → flat path.

    Migration is gradual; vaults with some scopes-as-projects and others
    not yet promoted must keep filing the unpromoted ones at the flat
    path until the project folder gets created.
    """
    assert resolve_surface_dir(
        tmp_path, "decisions", scope="ccat:nonexistent",
    ) == tmp_path / "decisions"


def test_resolve_surface_dir_flat_for_empty_scope(tmp_path):
    (tmp_path / "projects" / "anything").mkdir(parents=True)
    # Empty scope can't pick a project folder.
    assert resolve_surface_dir(
        tmp_path, "concepts", scope="",
    ) == tmp_path / "concepts"
