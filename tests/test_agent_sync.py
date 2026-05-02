"""Tests for AGENTS.md / orientation `## Agent guidance` sync (Phase 7)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lore_core.projects.agent_sync import (
    compute_sync_status,
    extract_agent_guidance,
    read_repo_agent_file,
    replace_agent_guidance,
    write_repo_agent_file,
)


# ---------------------------------------------------------------------------
# extract_agent_guidance
# ---------------------------------------------------------------------------


def test_extract_returns_section_body():
    text = (
        "---\ntype: project\n---\n\n"
        "# Project: foo\n\n"
        "## Overview\n\nblah blah\n\n"
        "## Agent guidance\n\n"
        "Use TDD.\nDo not commit secrets.\n\n"
        "## Architecture\n\narch\n"
    )
    body = extract_agent_guidance(text)
    assert body is not None
    assert "Use TDD." in body
    assert "Do not commit secrets." in body
    assert "Overview" not in body
    assert "Architecture" not in body


def test_extract_returns_none_when_section_absent():
    text = "# foo\n\n## Overview\n\nx\n"
    assert extract_agent_guidance(text) is None


def test_extract_handles_section_at_end_of_body():
    text = (
        "# foo\n\n"
        "## Overview\n\nx\n\n"
        "## Agent guidance\n\nUse TDD.\n"
    )
    body = extract_agent_guidance(text)
    assert body is not None
    assert "Use TDD." in body


# ---------------------------------------------------------------------------
# replace_agent_guidance
# ---------------------------------------------------------------------------


def test_replace_substitutes_existing_section():
    text = (
        "---\ntype: project\n---\n\n"
        "# foo\n\n"
        "## Overview\n\nover\n\n"
        "## Agent guidance\n\nold guidance\n\n"
        "## Architecture\n\narch\n"
    )
    out = replace_agent_guidance(text, "new guidance")
    assert "new guidance" in out
    assert "old guidance" not in out
    # Other sections preserved.
    assert "## Overview" in out
    assert "## Architecture" in out
    # Frontmatter preserved.
    assert "type: project" in out


def test_replace_preserves_frontmatter_with_yaml_strings_resembling_body():
    """Regression: ``rfind(body)`` produced wrong cuts when YAML contained
    a tail matching the body. The fix uses ``split_frontmatter`` instead.
    """
    text = (
        "---\n"
        "type: project\n"
        "description: 'see ## Overview'\n"  # YAML string mentions body markers
        "scope: lore\n"
        "---\n\n"
        "# foo\n\n"
        "## Overview\n\nover\n"
    )
    out = replace_agent_guidance(text, "fresh guidance")
    assert "type: project" in out
    assert "description: 'see ## Overview'" in out
    assert "scope: lore" in out
    assert "## Agent guidance" in out
    assert "fresh guidance" in out


def test_replace_handles_no_frontmatter_at_all():
    """Documents without frontmatter still get the section appended."""
    text = "# foo\n\n## Overview\n\nover\n"
    out = replace_agent_guidance(text, "fresh guidance")
    assert out.startswith("# foo")
    assert "## Agent guidance" in out
    assert "fresh guidance" in out


def test_replace_appends_when_section_missing():
    text = (
        "---\ntype: project\n---\n\n"
        "# foo\n\n"
        "## Overview\n\nover\n"
    )
    out = replace_agent_guidance(text, "fresh guidance")
    assert "## Agent guidance" in out
    assert "fresh guidance" in out
    assert "## Overview" in out


# ---------------------------------------------------------------------------
# read_repo_agent_file
# ---------------------------------------------------------------------------


def test_read_repo_prefers_agents_over_claude(tmp_path):
    (tmp_path / "AGENTS.md").write_text(
        "# myrepo\n\nUse TDD.\nFollow conventions.\n"
    )
    (tmp_path / "CLAUDE.md").write_text(
        "# myrepo\n\nDifferent content.\n"
    )
    path, body = read_repo_agent_file(tmp_path)
    assert path is not None
    assert path.name == "AGENTS.md"
    assert "Use TDD." in body
    # Leading H1 stripped.
    assert "# myrepo" not in body


def test_read_repo_falls_back_to_claude(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# myrepo\n\nClaude content.\n")
    path, body = read_repo_agent_file(tmp_path)
    assert path is not None
    assert path.name == "CLAUDE.md"
    assert "Claude content." in body


def test_read_repo_returns_none_when_no_files(tmp_path):
    path, body = read_repo_agent_file(tmp_path)
    assert path is None
    assert body == ""


# ---------------------------------------------------------------------------
# compute_sync_status
# ---------------------------------------------------------------------------


def test_in_sync_when_content_matches(tmp_path):
    orientation_dir = tmp_path / "wiki"
    orientation_dir.mkdir()
    orientation = orientation_dir / "orientation.md"
    orientation.write_text(
        "---\ntype: project\n---\n\n"
        "# foo\n\n"
        "## Agent guidance\n\nUse TDD.\nFollow conventions.\n"
    )

    repo = tmp_path / "myrepo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text(
        "# myrepo\n\nUse TDD.\nFollow conventions.\n"
    )

    status = compute_sync_status(orientation, repo)
    assert status.orientation_has_section is True
    assert status.repo_file_exists is True
    assert status.in_sync is True


def test_drift_when_content_differs(tmp_path):
    orientation = tmp_path / "orientation.md"
    orientation.write_text(
        "---\ntype: project\n---\n\n"
        "# foo\n\n"
        "## Agent guidance\n\nVersion A.\n"
    )

    repo = tmp_path / "myrepo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text(
        "# myrepo\n\nVersion B (different).\n"
    )

    status = compute_sync_status(orientation, repo)
    assert status.in_sync is False


def test_in_sync_when_orientation_has_no_section(tmp_path):
    """No ``## Agent guidance`` section means there's nothing to drift.
    The check passes silently (in_sync=True) so lint stays quiet."""
    orientation = tmp_path / "orientation.md"
    orientation.write_text(
        "---\ntype: project\n---\n\n# foo\n\n## Overview\n\nover\n"
    )
    repo = tmp_path / "myrepo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# myrepo\n\nrepo content.\n")

    status = compute_sync_status(orientation, repo)
    assert status.orientation_has_section is False
    assert status.in_sync is True


def test_normalisation_treats_whitespace_as_equivalent(tmp_path):
    """Trailing newlines, internal blank lines, indentation differences
    don't trigger drift."""
    orientation = tmp_path / "orientation.md"
    orientation.write_text(
        "---\ntype: project\n---\n\n"
        "# foo\n\n"
        "## Agent guidance\n\n  Use TDD.\n  Follow conventions.\n\n\n"
    )
    repo = tmp_path / "myrepo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text(
        "# myrepo\n\nUse TDD.\n\nFollow conventions.\n"
    )

    status = compute_sync_status(orientation, repo)
    assert status.in_sync is True


# ---------------------------------------------------------------------------
# write_repo_agent_file
# ---------------------------------------------------------------------------


def test_write_repo_agent_file_writes_with_h1_title(tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    target = repo / "AGENTS.md"
    write_repo_agent_file(target, "Some guidance.\n")

    text = target.read_text()
    assert text.startswith("# myrepo\n")
    assert "Some guidance." in text
