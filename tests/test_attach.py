"""Tests for the legacy ``## Lore`` CLAUDE.md parser + section remover.

Post-Phase-6, the parser (``lore_core.attach``) is only used by the
migration tool; the remover (``lore_cli.attach_cmd.remove_section``)
is shared between migration and ``lore detach``. These tests verify
both behave correctly on representative legacy files.

The legacy write path and its CLI (``lore attach write``) are gone —
replaced by ``lore attach accept / manual / offer`` in Phase 3.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lore_cli.attach_cmd import remove_section
from lore_core.attach import (
    SECTION_HEADING,
    find_section,
    parse_section_body,
    read_attach,
)


# ---- parser ----

def test_find_section_absent() -> None:
    assert find_section(["# Title", "", "Some prose."]) is None


def test_find_section_until_next_heading() -> None:
    lines = [
        "# Title",
        "",
        SECTION_HEADING,
        "",
        "- wiki: w",
        "- scope: a:b",
        "",
        "## Other",
        "",
        "Unrelated.",
    ]
    bounds = find_section(lines)
    assert bounds is not None
    start, end = bounds
    assert lines[start] == SECTION_HEADING
    assert lines[end] == "## Other"


def test_find_section_to_eof() -> None:
    lines = [SECTION_HEADING, "", "- wiki: w", "- scope: a:b"]
    bounds = find_section(lines)
    assert bounds == (0, 4)


def test_parse_section_body() -> None:
    body = [
        "<!-- comment -->",
        "",
        "- wiki: w",
        "- scope: a:b",
        "- backend: github",
        "- custom: value",
        "Not a bullet.",
    ]
    parsed = parse_section_body(body)
    assert parsed == {
        "wiki": "w",
        "scope": "a:b",
        "backend": "github",
        "custom": "value",
    }


def test_read_attach_full(tmp_path: Path) -> None:
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text(
        "# Project\n\n## Lore\n\n- wiki: team\n- scope: proj:mod\n"
    )
    block = read_attach(claude_md)
    assert block == {"wiki": "team", "scope": "proj:mod"}


def test_read_absent_returns_empty_dict(tmp_path: Path) -> None:
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("# No lore\n")
    assert read_attach(claude_md) == {}


def test_read_missing_file_returns_empty_dict(tmp_path: Path) -> None:
    assert read_attach(tmp_path / "nope.md") == {}


# ---- remove_section ----

def test_remove_section_strips_block_only(tmp_path: Path) -> None:
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text(
        "# Project\n\nSome prose.\n\n## Lore\n\n- wiki: team\n- scope: proj\n\n## Other\n\nKept.\n"
    )
    changed = remove_section(claude_md)
    assert changed
    text = claude_md.read_text()
    assert "## Lore" not in text
    assert "Some prose." in text
    assert "## Other" in text
    assert "Kept." in text


def test_remove_section_noop_when_absent(tmp_path: Path) -> None:
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("# No lore here\n")
    assert remove_section(claude_md) is False


def test_remove_section_missing_file(tmp_path: Path) -> None:
    assert remove_section(tmp_path / "nope.md") is False
