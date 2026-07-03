"""Tests for ``scopes.rewrite_scopes_in_frontmatter``."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from lore_core.schema import parse_frontmatter
from lore_core.scopes import rewrite_scopes_in_frontmatter


def _write_note(tmp_path: Path, name: str, fm: dict) -> Path:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    dumped = yaml.safe_dump(fm, sort_keys=False).strip()
    p.write_text(f"---\n{dumped}\n---\n\n# Body\n")
    return p


def test_rewrite_exact_match(tmp_path):
    p = _write_note(tmp_path, "concepts/foo.md", {
        "type": "concept", "scope": "ccat:data-center",
    })
    n = rewrite_scopes_in_frontmatter(tmp_path, {"ccat:data-center": "ccat:dc"})
    assert n == 1
    fm = parse_frontmatter(p.read_text())
    assert fm["scope"] == "ccat:dc"


def test_rewrite_subtree_cascade(tmp_path):
    """Renaming ``ccat:data-center`` cascades to ``ccat:data-center:ops-db``."""
    p1 = _write_note(tmp_path, "concepts/a.md", {
        "type": "concept", "scope": "ccat:data-center",
    })
    p2 = _write_note(tmp_path, "concepts/b.md", {
        "type": "concept", "scope": "ccat:data-center:ops-db",
    })
    p3 = _write_note(tmp_path, "concepts/c.md", {
        "type": "concept", "scope": "lore",
    })
    n = rewrite_scopes_in_frontmatter(tmp_path, {"ccat:data-center": "ccat:dc"})
    assert n == 2
    assert parse_frontmatter(p1.read_text())["scope"] == "ccat:dc"
    assert parse_frontmatter(p2.read_text())["scope"] == "ccat:dc:ops-db"
    # Untouched.
    assert parse_frontmatter(p3.read_text())["scope"] == "lore"


def test_rewrite_handles_scopes_list(tmp_path):
    """``scopes:`` (plural list) is also rewritten entry-by-entry."""
    p = _write_note(tmp_path, "concepts/x.md", {
        "type": "concept",
        "scopes": ["ccat:data-center:ops-db", "lore"],
    })
    n = rewrite_scopes_in_frontmatter(
        tmp_path, {"ccat:data-center": "ccat:dc"},
    )
    assert n == 1
    fm = parse_frontmatter(p.read_text())
    assert fm["scopes"] == ["ccat:dc:ops-db", "lore"]


def test_rewrite_no_match_no_change(tmp_path):
    p = _write_note(tmp_path, "concepts/a.md", {
        "type": "concept", "scope": "lore",
    })
    n = rewrite_scopes_in_frontmatter(tmp_path, {"ccat": "x"})
    assert n == 0
    assert parse_frontmatter(p.read_text())["scope"] == "lore"


def test_rewrite_handles_empty_mapping(tmp_path):
    _write_note(tmp_path, "concepts/a.md", {"type": "concept", "scope": "lore"})
    assert rewrite_scopes_in_frontmatter(tmp_path, {}) == 0


def test_rewrite_skips_files_without_frontmatter(tmp_path):
    """Plain markdown without YAML frontmatter is skipped silently."""
    bare = tmp_path / "concepts" / "raw.md"
    bare.parent.mkdir(parents=True)
    bare.write_text("# Just a title\n\nbody here.\n")
    n = rewrite_scopes_in_frontmatter(tmp_path, {"ccat": "x"})
    assert n == 0


def test_rewrite_preserves_body(tmp_path):
    p = _write_note(tmp_path, "concepts/a.md", {
        "type": "concept", "scope": "ccat:data-center",
    })
    # Add a richer body.
    text = p.read_text()
    p.write_text(text + "\n\nMore body content.\n")
    n = rewrite_scopes_in_frontmatter(tmp_path, {"ccat:data-center": "ccat:dc"})
    assert n == 1
    new_text = p.read_text()
    assert "More body content." in new_text


def test_rewrite_returns_zero_for_missing_wiki_root(tmp_path):
    nonexistent = tmp_path / "no-such-dir"
    assert rewrite_scopes_in_frontmatter(nonexistent, {"a": "b"}) == 0
