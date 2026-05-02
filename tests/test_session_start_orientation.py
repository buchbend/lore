"""Tests for SessionStart project orientation auto-injection (Phase 6).

When SessionStart fires inside an attached scope and a project
orientation note exists at ``projects/<slug>/<slug>.md`` (folder
layout) or ``projects/<slug>.md`` (legacy flat), the orientation body
is appended to the LLM context block.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lore_cli.hooks import (
    ORIENTATION_BUDGET_CHARS,
    _render_project_orientation,
)
from lore_core.types import Scope


def _scope(wiki: str, scope_str: str) -> Scope:
    return Scope(
        wiki=wiki,
        scope=scope_str,
        backend="none",
        claude_md_path=Path("/tmp/dummy/CLAUDE.md"),
    )


def test_no_orientation_returns_none(tmp_path):
    """Wiki has no projects/ at all → returns None, no error."""
    (tmp_path / "private").mkdir()
    result = _render_project_orientation(_scope("private", "lore"), tmp_path)
    assert result is None


def test_folder_layout_orientation_loaded(tmp_path):
    wiki = tmp_path / "private"
    project_dir = wiki / "projects" / "lore"
    project_dir.mkdir(parents=True)
    (project_dir / "lore.md").write_text(
        "---\ntype: project\nscope: lore\n---\n\n"
        "# Project: lore\n\n"
        "This is the lore project orientation.\n"
    )

    result = _render_project_orientation(_scope("private", "lore"), tmp_path)
    assert result is not None
    assert "[[lore]]" in result
    assert "lore project orientation" in result
    # Frontmatter is stripped.
    assert "type: project" not in result


def test_legacy_flat_orientation_loaded(tmp_path):
    wiki = tmp_path / "private"
    (wiki / "projects").mkdir(parents=True)
    (wiki / "projects" / "lore.md").write_text(
        "---\ntype: project\n---\n\n# Project: lore\n\nlegacy body\n"
    )

    result = _render_project_orientation(_scope("private", "lore"), tmp_path)
    assert result is not None
    assert "legacy body" in result


def test_folder_layout_preferred_over_flat(tmp_path):
    """When both ``projects/<slug>/<slug>.md`` and ``projects/<slug>.md``
    exist (mid-migration), the folder layout wins."""
    wiki = tmp_path / "private"
    (wiki / "projects" / "lore").mkdir(parents=True)
    (wiki / "projects" / "lore" / "lore.md").write_text(
        "---\ntype: project\n---\n\n# Project: lore\n\nfolder body\n"
    )
    (wiki / "projects" / "lore.md").write_text(
        "---\ntype: project\n---\n\n# Project: lore\n\nflat body\n"
    )

    result = _render_project_orientation(_scope("private", "lore"), tmp_path)
    assert result is not None
    assert "folder body" in result
    assert "flat body" not in result


def test_orientation_capped_at_budget(tmp_path):
    """Body longer than ORIENTATION_BUDGET_CHARS gets truncated with a
    user-facing hint pointing at /lore:context."""
    wiki = tmp_path / "private"
    project_dir = wiki / "projects" / "lore"
    project_dir.mkdir(parents=True)
    long_body = "x" * (ORIENTATION_BUDGET_CHARS + 200)
    (project_dir / "lore.md").write_text(
        "---\ntype: project\n---\n\n" + long_body
    )

    result = _render_project_orientation(_scope("private", "lore"), tmp_path)
    assert result is not None
    # The header adds some chars; the body is capped to ORIENTATION_BUDGET_CHARS.
    body_part = result.split("\n", 1)[1]
    assert len(body_part) <= ORIENTATION_BUDGET_CHARS
    assert "orientation truncated" in body_part


def test_orientation_uses_last_scope_segment(tmp_path):
    """``ccat:data-center:ops-db`` resolves to project slug ``ops-db``."""
    wiki = tmp_path / "ccat"
    (wiki / "projects" / "ops-db").mkdir(parents=True)
    (wiki / "projects" / "ops-db" / "ops-db.md").write_text(
        "---\ntype: project\n---\n\n# Project: ops-db\n\nThe ops-db project.\n"
    )

    result = _render_project_orientation(
        _scope("ccat", "ccat:data-center:ops-db"), tmp_path,
    )
    assert result is not None
    assert "[[ops-db]]" in result
    assert "ops-db project" in result


def test_empty_scope_returns_none(tmp_path):
    """Defensive: a scope with no scope-string returns None cleanly."""
    (tmp_path / "private").mkdir()
    result = _render_project_orientation(_scope("private", ""), tmp_path)
    assert result is None


def test_missing_wiki_root_returns_none(tmp_path):
    """If LORE_ROOT/wiki doesn't exist, returns None gracefully."""
    nonexistent = tmp_path / "no-such-wiki-root"
    result = _render_project_orientation(_scope("private", "lore"), nonexistent)
    assert result is None
