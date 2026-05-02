"""Tests for ``stub_project_note(bare=True)`` — Phase 3 Curator C
hoist auto-stub support.

Bare mode produces a folder-shaped project note at
``projects/<slug>/<slug>.md`` without reading any harness files. Used
by Curator C's cross-scope hoist pass when a parent scope project
folder needs to be materialised before drafts can land in it.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml

from lore_core.projects.stub_generator import StubResult, stub_project_note
from lore_core.schema import parse_frontmatter


def test_bare_writes_folder_shaped_path(tmp_path):
    result = stub_project_note(
        wiki_root=tmp_path,
        repo_slug="data-center",
        scope="ccat:data-center",
        bare=True,
        today=date(2026, 5, 1),
    )
    assert isinstance(result, StubResult)
    assert result.was_new is True
    expected = tmp_path / "projects" / "data-center" / "data-center.md"
    assert result.path == expected
    assert expected.exists()


def test_bare_skips_key_decisions_section(tmp_path):
    stub_project_note(
        wiki_root=tmp_path,
        repo_slug="data-center",
        scope="ccat:data-center",
        bare=True,
        today=date(2026, 5, 1),
    )
    target = tmp_path / "projects" / "data-center" / "data-center.md"
    body = target.read_text()
    # Bare-mode skeleton has Overview/Conventions/Architecture only.
    assert "## Overview" in body
    assert "## Conventions" in body
    assert "## Architecture" in body
    assert "## Key decisions" not in body, (
        "bare-mode stubs must skip ``Key decisions`` — that lives in "
        "projects/<slug>/decisions/ now."
    )


def test_bare_does_not_set_repo_field(tmp_path):
    """Bare stubs have no single repo behind them — the parent scope
    may aggregate multiple sub-projects. ``repo:`` frontmatter stays
    absent so the user can fill it in if appropriate."""
    stub_project_note(
        wiki_root=tmp_path,
        repo_slug="data-center",
        scope="ccat:data-center",
        bare=True,
        today=date(2026, 5, 1),
    )
    target = tmp_path / "projects" / "data-center" / "data-center.md"
    fm = parse_frontmatter(target.read_text())
    assert "repo" not in fm
    assert fm["type"] == "project"
    assert fm["scope"] == "ccat:data-center"


def test_bare_idempotent_on_re_call(tmp_path):
    """Calling bare stub twice doesn't error; second call refreshes in place."""
    first = stub_project_note(
        wiki_root=tmp_path,
        repo_slug="data-center",
        scope="ccat:data-center",
        bare=True,
        today=date(2026, 5, 1),
    )
    assert first.was_new is True

    second = stub_project_note(
        wiki_root=tmp_path,
        repo_slug="data-center",
        scope="ccat:data-center",
        bare=True,
        today=date(2026, 5, 2),
    )
    assert second.was_new is False
    assert second.path == first.path

    target = first.path
    fm = parse_frontmatter(target.read_text())
    assert fm["last_reviewed"] == "2026-05-02"


def test_bare_preserves_user_content_outside_canonical_sections(tmp_path):
    """User adds a custom ``## My Notes`` section between calls. The
    refresh must preserve it. Also ensures bare-mode does NOT clobber
    a ``## Key decisions`` section the user has authored manually."""
    target_dir = tmp_path / "projects" / "data-center"
    target_dir.mkdir(parents=True)
    target = target_dir / "data-center.md"
    target.write_text(
        "---\n"
        "schema_version: 2\n"
        "type: project\n"
        "created: '2026-05-01'\n"
        "last_reviewed: '2026-05-01'\n"
        "description: data center\n"
        "tags: [project]\n"
        "scope: ccat:data-center\n"
        "---\n\n"
        "# Project: data-center\n\n"
        "## Overview\n\nold overview\n\n"
        "## My Notes\n\nuser content here\n\n"
        "## Key decisions\n\nuser-authored decision\n"
    )

    stub_project_note(
        wiki_root=tmp_path,
        repo_slug="data-center",
        scope="ccat:data-center",
        bare=True,
        today=date(2026, 5, 5),
    )
    body = target.read_text()
    # User's custom section preserved.
    assert "## My Notes" in body
    assert "user content here" in body
    # User's manually-authored Key decisions left alone (bare mode does
    # not own that section).
    assert "user-authored decision" in body


def test_bare_requires_repo_root_when_not_bare(tmp_path):
    """Sanity: non-bare mode still requires repo_root."""
    with pytest.raises(ValueError, match="repo_root is required"):
        stub_project_note(
            wiki_root=tmp_path,
            repo_slug="ops-db",
            scope="ccat:ops-db",
            bare=False,
        )


def test_bare_handles_missing_scope(tmp_path):
    """Bare stubs work without an explicit scope (rare but legal)."""
    result = stub_project_note(
        wiki_root=tmp_path,
        repo_slug="orphan",
        scope=None,
        bare=True,
        today=date(2026, 5, 1),
    )
    assert result.path.exists()
    fm = parse_frontmatter(result.path.read_text())
    assert "scope" not in fm
