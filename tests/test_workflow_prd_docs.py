"""Tests for `lore_workflow.prd_docs` — the PRD-file creation mechanic.

Ported from ccat-agent-workflow's `tests/test_to_epic_prd.py`. Only the
helper's behavioural half is ported; that repo's structural SKILL.md
prose-gate checks are skill-specific and out of scope here (skills are
ported separately, #172).
"""

from __future__ import annotations

import re
from pathlib import Path

from lore_workflow import prd_docs as mod


def _parse_frontmatter(text: str) -> str | None:
    """Return the raw YAML frontmatter block, or None if absent."""
    if not text.startswith("---"):
        return None
    match = re.match(r"^---\s*\n(.*?)\n---\s*(\n|$)", text, re.DOTALL)
    return match.group(1) if match else None


def test_create_prd_writes_file(tmp_path: Path) -> None:
    path = mod.create_prd(
        tmp_path,
        slug="foo",
        title="Foo",
        epic_url="https://github.com/o/r/issues/8",
        repos=["o/r"],
    )
    assert Path(path).exists()
    assert (tmp_path / "docs" / "prd" / "0001-foo.md").exists()


def test_create_prd_path_is_nnnn_kebab(tmp_path: Path) -> None:
    """The PRD file must be docs/prd/NNNN-kebab.md (zero-padded sequence)."""
    path = mod.create_prd(
        tmp_path,
        slug="foo",
        title="Foo",
        epic_url="https://github.com/o/r/issues/8",
        repos=["o/r"],
    )
    assert Path(path).name == "0001-foo.md"


def test_create_prd_frontmatter_links_epic(tmp_path: Path) -> None:
    """The PRD front-matter must carry an epic: link."""
    epic_url = "https://github.com/o/r/issues/8"
    mod.create_prd(tmp_path, slug="foo", title="Foo", epic_url=epic_url, repos=["o/r"])
    text = (tmp_path / "docs" / "prd" / "0001-foo.md").read_text(encoding="utf-8")
    fm = _parse_frontmatter(text)
    assert fm is not None, "PRD must open with MyST/YAML front-matter"
    assert "epic:" in fm, "PRD front-matter must carry an epic: key"
    assert epic_url in fm, "PRD front-matter epic: must hold the epic URL"


def test_create_prd_frontmatter_lists_repos(tmp_path: Path) -> None:
    """The PRD front-matter must list every involved repo."""
    mod.create_prd(
        tmp_path,
        slug="foo",
        title="Foo",
        epic_url="https://github.com/o/r/issues/8",
        repos=["o/r", "o/other"],
    )
    text = (tmp_path / "docs" / "prd" / "0001-foo.md").read_text(encoding="utf-8")
    fm = _parse_frontmatter(text)
    assert fm is not None
    assert "repos:" in fm, "PRD front-matter must carry a repos: list"
    assert "o/r" in fm and "o/other" in fm, "all involved repos must be listed"


def test_create_prd_has_title_and_problem(tmp_path: Path) -> None:
    """The PRD body must carry the title and the PRD skeleton sections."""
    mod.create_prd(
        tmp_path,
        slug="widget-export",
        title="Widget export",
        epic_url="https://github.com/o/r/issues/8",
        repos=["o/r"],
    )
    text = (tmp_path / "docs" / "prd" / "0001-widget-export.md").read_text(
        encoding="utf-8"
    )
    assert "Widget export" in text
    assert "## Problem" in text


def test_create_prd_sequence_increments(tmp_path: Path) -> None:
    """A second PRD in the same repo gets the next zero-padded number."""
    mod.create_prd(tmp_path, slug="first", title="First", epic_url="u", repos=["o/r"])
    second = mod.create_prd(
        tmp_path, slug="second", title="Second", epic_url="u", repos=["o/r"]
    )
    assert Path(second).name == "0002-second.md"


# ---------------------------------------------------------------------------
# Toctree wiring
# ---------------------------------------------------------------------------


def test_create_prd_wires_toctree(tmp_path: Path) -> None:
    """Creating 0001-foo.md wires 0001-foo into docs/prd/index.md's toctree."""
    mod.create_prd(tmp_path, slug="foo", title="Foo", epic_url="u", repos=["o/r"])
    index = (tmp_path / "docs" / "prd" / "index.md").read_text(encoding="utf-8")
    assert "```{toctree}" in index, "prd index must use single-brace ```{toctree}"
    assert "{{" not in index, "prd index must not contain double braces"
    assert "0001-foo" in index, "the PRD must be wired into the toctree"


def test_create_prd_wiring_idempotent(tmp_path: Path) -> None:
    """Wiring the same PRD twice must not duplicate its toctree entry."""
    mod.create_prd(tmp_path, slug="foo", title="Foo", epic_url="u", repos=["o/r"])
    mod.wire_toctree(tmp_path / "docs" / "prd" / "index.md", "0001-foo")
    index = (tmp_path / "docs" / "prd" / "index.md").read_text(encoding="utf-8")
    assert index.count("0001-foo") == 1, "PRD entry must appear exactly once"


def test_create_prd_preserves_existing_index_content(tmp_path: Path) -> None:
    """An existing docs/prd/index.md (with entries) is preserved, not clobbered."""
    prd_dir = tmp_path / "docs" / "prd"
    prd_dir.mkdir(parents=True)
    existing = "# PRDs\n\n```{toctree}\n:maxdepth: 1\n\n0001-existing\n```\n"
    (prd_dir / "index.md").write_text(existing, encoding="utf-8")
    mod.create_prd(tmp_path, slug="new", title="New", epic_url="u", repos=["o/r"])
    index = (prd_dir / "index.md").read_text(encoding="utf-8")
    assert "0001-existing" in index, "existing toctree entries must survive"
    assert "0002-new" in index, "new PRD must be wired in and numbered next"


def test_create_prd_creates_index_when_absent(tmp_path: Path) -> None:
    """When docs/prd/index.md is missing, the helper creates a wired one."""
    mod.create_prd(tmp_path, slug="foo", title="Foo", epic_url="u", repos=["o/r"])
    index = tmp_path / "docs" / "prd" / "index.md"
    assert index.exists(), "helper must create docs/prd/index.md when absent"
    assert "0001-foo" in index.read_text(encoding="utf-8")
