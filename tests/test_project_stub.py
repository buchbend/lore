"""Tests for projects/harness_parser.py and projects/stub_generator.py.

Covers:

* All six README-description branches (badge-prefixed, HTML-prefixed,
  no-prose fallback chain, normal, oversized capped, pyproject-only).
* Conventions extraction with attribution headings.
* Architecture detection (present + absent).
* Fresh stub from CLAUDE.md + README + pyproject.
* Idempotent re-stub: canonical-heading regeneration preserves
  user content under any other heading.
"""
from __future__ import annotations

from datetime import date as _date
from pathlib import Path

import pytest

from lore_core.projects.harness_parser import (
    HarnessSections,
    extract_description,
    parse_harness_files,
)
from lore_core.projects.stub_generator import CANONICAL_SECTIONS, stub_project_note
from lore_core.schema import parse_frontmatter, strip_frontmatter


# ---------------------------------------------------------------------------
# README description algorithm — six branches
# ---------------------------------------------------------------------------


def test_description_normal_readme() -> None:
    """Plain prose first paragraph with ≥40 chars wins."""
    readme = (
        "# My Project\n\n"
        "This is the project description sentence with enough characters to qualify.\n\n"
        "More body content.\n"
    )
    assert (
        extract_description(readme=readme)
        == "This is the project description sentence with enough characters to qualify."
    )


def test_description_skips_badge_lines() -> None:
    readme = (
        "# Project\n\n"
        "![CI](https://example.com/ci.svg) ![PyPI](https://example.com/pypi.svg)\n\n"
        "Real project description sentence with enough characters in it now.\n"
    )
    desc = extract_description(readme=readme)
    assert desc == "Real project description sentence with enough characters in it now."


def test_description_skips_html_block() -> None:
    """READMEs starting with `<p align="center">…</p>` HTML blocks."""
    readme = (
        "# P\n\n"
        '<p align="center">\n'
        '  <img src="logo.png" />\n'
        "</p>\n\n"
        "Description prose comes after the HTML block, definitely long enough.\n"
    )
    desc = extract_description(readme=readme)
    assert desc.startswith("Description prose comes after")


def test_description_falls_back_to_pyproject() -> None:
    """README with no qualifying prose → pyproject [project] description wins."""
    pyproject = (
        '[project]\nname = "x"\n'
        'description = "From pyproject — sufficiently long sentence for description."\n'
    )
    desc = extract_description(readme="# Just a heading\n", pyproject_text=pyproject)
    assert desc == "From pyproject — sufficiently long sentence for description."


def test_description_falls_back_to_package_json() -> None:
    package_json = (
        '{\n  "name": "x",\n  "description": "From package.json — adequately long."\n}\n'
    )
    desc = extract_description(readme=None, package_json_text=package_json)
    assert desc == "From package.json — adequately long."


def test_description_falls_back_to_literal() -> None:
    """No README, no pyproject, no package.json → literal "Project: <slug>"."""
    desc = extract_description(fallback_repo_slug="my-repo")
    assert desc == "Project: my-repo"


def test_description_caps_oversized_with_ellipsis() -> None:
    """First paragraph longer than 200 chars gets truncated."""
    long_line = "x " * 150  # well over 200 chars
    desc = extract_description(readme=f"# P\n\n{long_line}\n")
    assert len(desc) <= 200
    assert desc.endswith("…")


# ---------------------------------------------------------------------------
# Conventions + Architecture extraction
# ---------------------------------------------------------------------------


def test_conventions_combines_sources_with_attribution() -> None:
    sections = parse_harness_files(
        readme=None,
        claude_md="# CLAUDE.md\n\nUse 4-space indents.\n",
        agents_md="# AGENTS.md\n\nWrite tests first.\n",
        cursorrules="No emojis in code.\n",
        copilot_instructions=None,
        fallback_repo_slug="repo",
    )
    assert "CLAUDE.md" in sections.conventions
    assert "AGENTS.md" in sections.conventions
    assert ".cursorrules" in sections.conventions
    assert "copilot" not in sections.conventions  # absent source skipped
    assert "Use 4-space indents" in sections.conventions
    assert "Write tests first" in sections.conventions


def test_conventions_truncates_oversized_input() -> None:
    """A gigantic CLAUDE.md gets truncated so the project note stays scannable."""
    huge = "x" * 5000
    sections = parse_harness_files(
        claude_md=f"# CLAUDE.md\n\n{huge}\n",
        fallback_repo_slug="r",
    )
    assert "_(truncated;" in sections.conventions
    assert len(sections.conventions) < 5000


def test_architecture_extracts_when_present() -> None:
    claude_md = (
        "# CLAUDE.md\n\n"
        "## Conventions\nstuff\n\n"
        "## Architecture\n\nThe service is split into these modules.\n\n"
        "## Other\n\nUnrelated.\n"
    )
    sections = parse_harness_files(claude_md=claude_md, fallback_repo_slug="r")
    assert "split into these modules" in sections.architecture
    assert "Unrelated" not in sections.architecture


def test_architecture_empty_when_absent() -> None:
    sections = parse_harness_files(
        claude_md="# CLAUDE.md\n\n## Conventions\nx\n",
        fallback_repo_slug="r",
    )
    assert sections.architecture == ""


# ---------------------------------------------------------------------------
# Fresh stub generation
# ---------------------------------------------------------------------------


@pytest.fixture
def repo_with_files(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text(
        "# My Project\n\n"
        "A description sentence with enough text to qualify as the description.\n\n"
        "Paragraph two of overview content.\n"
    )
    (repo / "CLAUDE.md").write_text(
        "# CLAUDE.md\n\nUse the existing helpers; never reimplement.\n"
    )
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndescription = "fallback only"\n'
    )
    return repo


def test_stub_fresh_writes_all_sections(
    tmp_path: Path, repo_with_files: Path
) -> None:
    wiki = tmp_path / "wiki" / "private"
    result = stub_project_note(
        wiki_root=wiki,
        repo_root=repo_with_files,
        repo_slug="org/myrepo",
        scope="private",
        today=_date(2026, 4, 28),
    )
    assert result.was_new is True
    assert result.path.exists()
    body = strip_frontmatter(result.path.read_text())
    for heading in CANONICAL_SECTIONS:
        assert f"## {heading}" in body
    # Repo slug org/ stripped for display.
    assert "# Project: myrepo" in body


def test_stub_frontmatter_shape(tmp_path: Path, repo_with_files: Path) -> None:
    wiki = tmp_path / "wiki" / "private"
    result = stub_project_note(
        wiki_root=wiki,
        repo_root=repo_with_files,
        repo_slug="lore",
        scope="lore:dev",
        today=_date(2026, 4, 28),
    )
    fm = parse_frontmatter(result.path.read_text())
    assert fm["type"] == "project"
    assert fm["repo"] == "lore"
    assert fm["scope"] == "lore:dev"
    assert fm["created"] == "2026-04-28"
    assert fm["last_reviewed"] == "2026-04-28"
    assert fm["tags"] == ["project"]
    assert "description" in fm


def test_stub_omits_active_plans_section(
    tmp_path: Path, repo_with_files: Path
) -> None:
    """No `## Active plans` heading in the body — SessionStart owns that surface."""
    wiki = tmp_path / "wiki" / "private"
    result = stub_project_note(
        wiki_root=wiki, repo_root=repo_with_files, repo_slug="r"
    )
    body = strip_frontmatter(result.path.read_text())
    assert "Active plans" not in body


def test_stub_with_minimal_repo(tmp_path: Path) -> None:
    """Repo with NO harness files — stub still writes with placeholders."""
    repo = tmp_path / "repo"
    repo.mkdir()
    wiki = tmp_path / "wiki" / "x"
    result = stub_project_note(
        wiki_root=wiki, repo_root=repo, repo_slug="bare-repo"
    )
    assert result.was_new
    body = strip_frontmatter(result.path.read_text())
    assert "_No README found" in body
    assert "_No CLAUDE.md" in body


# ---------------------------------------------------------------------------
# Idempotent re-stub: canonical-heading regeneration preserves user content
# ---------------------------------------------------------------------------


def test_restub_preserves_user_content_under_other_headings(
    tmp_path: Path, repo_with_files: Path
) -> None:
    wiki = tmp_path / "wiki" / "private"
    # First stub.
    result1 = stub_project_note(
        wiki_root=wiki,
        repo_root=repo_with_files,
        repo_slug="r",
        today=_date(2026, 4, 28),
    )
    # User adds a custom heading + content between Conventions and Architecture.
    text = result1.path.read_text()
    text = text.replace(
        "## Architecture",
        "## Personal notes\n\nMy random thoughts about the project.\n\n## Architecture",
    )
    result1.path.write_text(text)

    # Re-stub. Personal-notes section MUST survive.
    result2 = stub_project_note(
        wiki_root=wiki,
        repo_root=repo_with_files,
        repo_slug="r",
        today=_date(2026, 5, 15),
    )
    assert result2.was_new is False
    body_after = result2.path.read_text()
    assert "## Personal notes" in body_after
    assert "My random thoughts" in body_after


def test_restub_refreshes_canonical_sections(
    tmp_path: Path, repo_with_files: Path
) -> None:
    wiki = tmp_path / "wiki" / "private"
    stub_project_note(
        wiki_root=wiki, repo_root=repo_with_files, repo_slug="r",
        today=_date(2026, 4, 28),
    )
    # User changes README → re-stub picks it up under ## Overview.
    (repo_with_files / "README.md").write_text(
        "# My Project\n\nCompletely new description sentence with enough characters now.\n"
    )
    result2 = stub_project_note(
        wiki_root=wiki, repo_root=repo_with_files, repo_slug="r",
        today=_date(2026, 5, 15),
    )
    body = strip_frontmatter(result2.path.read_text())
    assert "Completely new description" in body


def test_restub_preserves_user_description_edit(
    tmp_path: Path, repo_with_files: Path
) -> None:
    """If the user edited frontmatter `description:`, re-stub keeps it."""
    wiki = tmp_path / "wiki" / "private"
    result1 = stub_project_note(
        wiki_root=wiki, repo_root=repo_with_files, repo_slug="r"
    )
    # Replace the full description line with a custom user value.
    text = result1.path.read_text()
    import re as _re
    text = _re.sub(
        r"^description:.*$",
        "description: User-edited description",
        text,
        count=1,
        flags=_re.MULTILINE,
    )
    result1.path.write_text(text)

    stub_project_note(wiki_root=wiki, repo_root=repo_with_files, repo_slug="r")
    fm = parse_frontmatter(result1.path.read_text())
    assert fm["description"] == "User-edited description"


def test_restub_refreshes_last_reviewed(
    tmp_path: Path, repo_with_files: Path
) -> None:
    wiki = tmp_path / "wiki" / "private"
    stub_project_note(
        wiki_root=wiki, repo_root=repo_with_files, repo_slug="r",
        today=_date(2026, 4, 28),
    )
    result2 = stub_project_note(
        wiki_root=wiki, repo_root=repo_with_files, repo_slug="r",
        today=_date(2026, 5, 15),
    )
    fm = parse_frontmatter(result2.path.read_text())
    assert fm["last_reviewed"] == "2026-05-15"


def test_restub_when_user_renamed_canonical_heading(
    tmp_path: Path, repo_with_files: Path
) -> None:
    """User renamed `## Overview` → `## What this is`. Re-stub appends a fresh `## Overview`.

    Visible drift, easy to fix, never silent. The user's renamed
    section is preserved verbatim.
    """
    wiki = tmp_path / "wiki" / "private"
    result1 = stub_project_note(
        wiki_root=wiki, repo_root=repo_with_files, repo_slug="r"
    )
    text = result1.path.read_text()
    text = text.replace("## Overview", "## What this is")
    result1.path.write_text(text)

    result2 = stub_project_note(
        wiki_root=wiki, repo_root=repo_with_files, repo_slug="r"
    )
    body = result2.path.read_text()
    # User's renamed heading still there.
    assert "## What this is" in body
    # And a fresh ## Overview was appended (since the canonical heading was missing).
    assert body.count("## Overview") == 1
