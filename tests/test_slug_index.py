"""Tests for the catalog `slug_index` (Phase 1.2).

Covers:
- ``build_catalog`` emits a top-level ``slug_index: {stem: relpath}``
- duplicate stems produce a ``duplicate_stem`` lint WARNING
- the slug_index is deterministic (sorted iteration → first-by-path wins)
- ``_resolve_slug`` prefers slug_index over section iteration over rglob
- pre-Phase-1.2 catalogs (no slug_index field) still resolve via section iteration
"""

from __future__ import annotations

import json
from pathlib import Path

from lore_core.lint import Issue, NoteInfo, build_catalog
from lore_mcp.server import _resolve_slug


def _note(path: str, stem: str) -> NoteInfo:
    return NoteInfo(path=path, filename=stem, wiki="demo")


def test_slug_index_is_top_level(tmp_path):
    notes = [
        _note("concepts/astronomy.md", "astronomy"),
        _note("decisions/auth-rewrite.md", "auth-rewrite"),
    ]
    catalog = build_catalog("demo", notes, [])
    assert "slug_index" in catalog
    assert catalog["slug_index"]["astronomy"] == "concepts/astronomy.md"
    assert catalog["slug_index"]["auth-rewrite"] == "decisions/auth-rewrite.md"


def test_duplicate_stem_first_wins_deterministic():
    notes = [
        _note("decisions/curator.md", "curator"),
        _note("concepts/curator.md", "curator"),
    ]
    issues: list[Issue] = []
    catalog = build_catalog("demo", notes, issues)
    # Sorted by path → concepts/ comes before decisions/.
    assert catalog["slug_index"]["curator"] == "concepts/curator.md"


def test_duplicate_stem_emits_warning():
    notes = [
        _note("concepts/curator.md", "curator"),
        _note("decisions/curator.md", "curator"),
    ]
    issues: list[Issue] = []
    build_catalog("demo", notes, issues)
    dup_issues = [i for i in issues if i.check == "duplicate_stem"]
    assert len(dup_issues) == 1
    assert dup_issues[0].severity == "WARNING"
    assert "concepts/curator.md" in dup_issues[0].message
    assert "decisions/curator.md" in dup_issues[0].message


def test_no_warning_when_stems_unique():
    notes = [
        _note("concepts/a.md", "a"),
        _note("concepts/b.md", "b"),
    ]
    issues: list[Issue] = []
    build_catalog("demo", notes, issues)
    assert not any(i.check == "duplicate_stem" for i in issues)


def test_slug_index_appears_in_stats_warnings_count():
    """duplicate_stem warning should be reflected in stats.warnings."""
    notes = [
        _note("concepts/curator.md", "curator"),
        _note("decisions/curator.md", "curator"),
    ]
    catalog = build_catalog("demo", notes, [])
    assert catalog["stats"]["warnings"] >= 1


def test_resolver_uses_slug_index_when_present(tmp_path: Path, monkeypatch):
    """When the catalog has slug_index, resolver returns its mapping
    even if section iteration would have given a different answer."""
    wiki = tmp_path / "wiki" / "demo"
    wiki.mkdir(parents=True)
    (wiki / "concepts").mkdir()
    (wiki / "concepts" / "real.md").write_text("body")

    catalog = {
        "slug_index": {"real": "concepts/real.md"},
        "sections": {
            # Decoy entry — slug_index must take precedence.
            "concepts": [{"name": "real", "path": "wrong/path.md"}],
        },
    }
    (wiki / "_catalog.json").write_text(json.dumps(catalog))

    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    assert _resolve_slug(wiki, "real") == "concepts/real.md"


def test_resolver_falls_back_to_section_iteration(tmp_path: Path, monkeypatch):
    """Pre-Phase-1.2 catalogs (no slug_index) still resolve via sections."""
    wiki = tmp_path / "wiki" / "demo"
    wiki.mkdir(parents=True)

    catalog = {
        "sections": {
            "concepts": [{"name": "old-note", "path": "concepts/old-note.md"}],
        },
    }
    (wiki / "_catalog.json").write_text(json.dumps(catalog))

    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    assert _resolve_slug(wiki, "old-note") == "concepts/old-note.md"


def test_resolver_falls_back_to_rglob_for_uncatalogued(tmp_path: Path, monkeypatch):
    """Notes not in the catalog (drafts, freshly-written) still resolve."""
    wiki = tmp_path / "wiki" / "demo"
    wiki.mkdir(parents=True)
    (wiki / "drafts").mkdir()
    (wiki / "drafts" / "scratchpad.md").write_text("body")

    # Empty catalog — slug not in slug_index nor in sections.
    catalog = {"slug_index": {}, "sections": {}}
    (wiki / "_catalog.json").write_text(json.dumps(catalog))

    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    assert _resolve_slug(wiki, "scratchpad") == "drafts/scratchpad.md"


def test_resolver_returns_none_when_truly_missing(tmp_path: Path, monkeypatch):
    wiki = tmp_path / "wiki" / "demo"
    wiki.mkdir(parents=True)
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    assert _resolve_slug(wiki, "nonexistent") is None
