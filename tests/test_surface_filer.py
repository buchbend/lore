"""Tests for lore_curator.surface_filer — surface note writer."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from lore_core.surfaces import SurfaceDef, SurfacesDoc
from lore_curator.surface_filer import FiledSurface, file_surface


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_surfaces_doc(tmp_path: Path) -> SurfacesDoc:
    """Build a SurfacesDoc with concept, decision, paper, result surfaces."""
    return SurfacesDoc(
        schema_version=2,
        surfaces=[
            SurfaceDef(
                name="concept",
                description="Cross-cutting idea or pattern.",
                required=["schema_version", "type", "created", "last_reviewed", "description", "tags"],
            ),
            SurfaceDef(
                name="decision",
                description="A trade-off made.",
                required=["schema_version", "type", "created", "last_reviewed", "description", "tags"],
            ),
            SurfaceDef(
                name="paper",
                description="A research paper reference.",
                required=["schema_version", "type", "citekey", "title", "authors", "year", "description", "tags"],
            ),
            SurfaceDef(
                name="result",
                description="An experimental result.",
                required=["schema_version", "type", "created", "last_reviewed", "description", "tags", "source_session"],
            ),
        ],
        path=tmp_path / "SURFACES.md",
    )


def _parse_frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    return yaml.safe_load(text[3:end]) or {}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_file_surface_creates_file_with_frontmatter(tmp_path):
    """concept surface → file exists with correct frontmatter fields."""
    doc = _make_surfaces_doc(tmp_path)
    result = file_surface(
        surface_name="concept",
        title="Test Concept",
        body="Body text",
        sources=["[[s1]]"],
        wiki_root=tmp_path,
        surfaces_doc=doc,
    )
    assert result.path.exists()
    fm = _parse_frontmatter(result.path.read_text())
    assert fm["type"] == "concept"
    assert fm["description"] == "Test Concept"
    assert fm["draft"] is True
    assert fm["synthesis_sources"] == ["[[s1]]"]


def test_file_surface_sets_draft_true_even_when_extra_says_false(tmp_path):
    """draft:true forced even if caller passes extra_frontmatter={"draft": False}."""
    doc = _make_surfaces_doc(tmp_path)
    result = file_surface(
        surface_name="concept",
        title="Forced Draft",
        body="body",
        sources=[],
        wiki_root=tmp_path,
        surfaces_doc=doc,
        extra_frontmatter={"draft": False},
    )
    fm = _parse_frontmatter(result.path.read_text())
    assert fm["draft"] is True


def test_file_surface_writes_to_correct_subdir_concept(tmp_path):
    """concept → concepts/ subdir."""
    doc = _make_surfaces_doc(tmp_path)
    result = file_surface(
        surface_name="concept",
        title="My Concept",
        body="body",
        sources=[],
        wiki_root=tmp_path,
        surfaces_doc=doc,
    )
    assert result.path.parent == tmp_path / "concepts"


def test_file_surface_writes_to_correct_subdir_decision(tmp_path):
    """decision → decisions/ subdir."""
    doc = _make_surfaces_doc(tmp_path)
    result = file_surface(
        surface_name="decision",
        title="My Decision",
        body="body",
        sources=[],
        wiki_root=tmp_path,
        surfaces_doc=doc,
    )
    assert result.path.parent == tmp_path / "decisions"


def test_file_surface_writes_to_correct_subdir_paper(tmp_path):
    """paper → papers/ subdir (custom required fields supplied via extras)."""
    doc = _make_surfaces_doc(tmp_path)
    result = file_surface(
        surface_name="paper",
        title="Some Paper",
        body="abstract",
        sources=[],
        wiki_root=tmp_path,
        surfaces_doc=doc,
        extra_frontmatter={
            "citekey": "smith2024",
            "title": "Some Paper",
            "authors": ["Smith"],
            "year": 2024,
        },
    )
    assert result.path.parent == tmp_path / "papers"


def test_file_surface_writes_to_correct_subdir_result(tmp_path):
    """result → results/ subdir."""
    doc = _make_surfaces_doc(tmp_path)
    result = file_surface(
        surface_name="result",
        title="My Result",
        body="body",
        sources=[],
        wiki_root=tmp_path,
        surfaces_doc=doc,
        extra_frontmatter={"source_session": "[[2026-04-01-session]]"},
    )
    assert result.path.parent == tmp_path / "results"


def test_file_surface_includes_synthesis_sources(tmp_path):
    """synthesis_sources frontmatter includes all wikilinks passed."""
    doc = _make_surfaces_doc(tmp_path)
    sources = ["[[2026-04-01-session]]", "[[2026-04-02-session]]"]
    result = file_surface(
        surface_name="concept",
        title="Source Test",
        body="body",
        sources=sources,
        wiki_root=tmp_path,
        surfaces_doc=doc,
    )
    fm = _parse_frontmatter(result.path.read_text())
    assert fm["synthesis_sources"] == sources


def test_file_surface_collision_appends_counter(tmp_path):
    """Two calls with same title → slug.md and slug-2.md."""
    doc = _make_surfaces_doc(tmp_path)
    kwargs = dict(
        surface_name="concept",
        title="Collision Test",
        body="body",
        sources=[],
        wiki_root=tmp_path,
        surfaces_doc=doc,
    )
    r1 = file_surface(**kwargs)
    r2 = file_surface(**kwargs)
    assert r1.path != r2.path
    assert r1.path.stem == "collision-test"
    assert r2.path.stem == "collision-test-2"


def test_file_surface_raises_on_missing_required_field(tmp_path):
    """paper without citekey in extras → ValueError."""
    doc = _make_surfaces_doc(tmp_path)
    with pytest.raises(ValueError, match="missing required frontmatter"):
        file_surface(
            surface_name="paper",
            title="Missing Fields Paper",
            body="body",
            sources=[],
            wiki_root=tmp_path,
            surfaces_doc=doc,
            # citekey, title, authors, year all missing
        )


def test_file_surface_custom_surface_with_empty_required_does_not_raise_keyerror(tmp_path):
    """Regression: a custom surface declared in SURFACES.md without a YAML block
    (so SurfaceDef.required is empty) AND not in legacy REQUIRED_FIELDS must NOT
    raise KeyError out of file_surface — it must succeed and produce a note with
    only the default frontmatter fields populated.

    Pre-fix: required_fields_for("custom_thing", wiki_dir=…) raised KeyError;
    file_surface didn't catch it; KeyError leaked out of the curator lock block.
    Post-fix: the fallback catches KeyError and treats it as "no required fields".
    """
    from lore_core.surfaces import SurfaceDef, SurfacesDoc

    doc = SurfacesDoc(
        schema_version=2,
        surfaces=[
            SurfaceDef(
                name="custom_thing",
                description="A custom surface declared with no YAML block.",
                required=[],         # empty — triggers the fallback path
                optional=[],
            ),
        ],
        path=tmp_path / "SURFACES.md",
    )
    result = file_surface(
        surface_name="custom_thing",
        title="Custom",
        body="Body",
        sources=[],
        wiki_root=tmp_path,
        surfaces_doc=doc,
    )
    assert result.path.exists()
    assert result.path.parent.name == "custom_things"


def test_file_surface_raises_on_unknown_surface_name(tmp_path):
    """Unknown surface name → ValueError listing declared names."""
    doc = _make_surfaces_doc(tmp_path)
    with pytest.raises(ValueError, match="not declared in SURFACES.md"):
        file_surface(
            surface_name="nonsense",
            title="Unknown",
            body="body",
            sources=[],
            wiki_root=tmp_path,
            surfaces_doc=doc,
        )


def test_file_surface_filed_wikilink_is_stem(tmp_path):
    """FiledSurface.wikilink matches [[{path.stem}]]."""
    doc = _make_surfaces_doc(tmp_path)
    result = file_surface(
        surface_name="concept",
        title="Wikilink Test",
        body="body",
        sources=[],
        wiki_root=tmp_path,
        surfaces_doc=doc,
    )
    assert result.wikilink == f"[[{result.path.stem}]]"


def test_file_surface_extra_frontmatter_overrides_defaults(tmp_path):
    """extra_frontmatter overrides default description and tags."""
    doc = _make_surfaces_doc(tmp_path)
    result = file_surface(
        surface_name="concept",
        title="Original Title",
        body="body",
        sources=[],
        wiki_root=tmp_path,
        surfaces_doc=doc,
        extra_frontmatter={"tags": ["custom"], "description": "Override"},
    )
    fm = _parse_frontmatter(result.path.read_text())
    assert fm["description"] == "Override"
    assert fm["tags"] == ["custom"]


def test_file_surface_uses_provided_now_for_created(tmp_path):
    """now parameter sets the created date in frontmatter."""
    doc = _make_surfaces_doc(tmp_path)
    fixed_now = datetime(2030, 1, 1, tzinfo=UTC)
    result = file_surface(
        surface_name="concept",
        title="Time Test",
        body="body",
        sources=[],
        wiki_root=tmp_path,
        surfaces_doc=doc,
        now=fixed_now,
    )
    fm = _parse_frontmatter(result.path.read_text())
    assert fm["created"] == "2030-01-01"


def test_file_surface_uses_plural_override(tmp_path):
    from lore_core.surfaces import SurfaceDef, SurfacesDoc
    from lore_curator.surface_filer import file_surface
    from pathlib import Path
    surface_def = SurfaceDef(
        name="study",
        description="",
        required=["type", "created", "description"],
        optional=[],
        plural="studies",
    )
    doc = SurfacesDoc(schema_version=2, surfaces=[surface_def], path=Path("<test>"))
    filed = file_surface(
        surface_name="study",
        title="My study",
        body="",
        sources=[],
        wiki_root=tmp_path,
        surfaces_doc=doc,
    )
    assert filed.path.parent.name == "studies"


def test_file_surface_defaults_to_pluralise_when_no_override(tmp_path):
    from lore_core.surfaces import SurfaceDef, SurfacesDoc
    from lore_curator.surface_filer import file_surface
    from pathlib import Path
    surface_def = SurfaceDef(
        name="concept",
        description="",
        required=["type", "created", "description"],
        optional=[],
    )
    doc = SurfacesDoc(schema_version=2, surfaces=[surface_def], path=Path("<test>"))
    filed = file_surface(
        surface_name="concept",
        title="X",
        body="",
        sources=[],
        wiki_root=tmp_path,
        surfaces_doc=doc,
    )
    assert filed.path.parent.name == "concepts"


def test_file_surface_uses_slug_format_with_frontmatter_field(tmp_path):
    from lore_core.surfaces import SurfaceDef, SurfacesDoc
    from lore_curator.surface_filer import file_surface
    from pathlib import Path
    surface_def = SurfaceDef(
        name="paper",
        description="",
        required=["type", "citekey", "title", "created", "description"],
        optional=[],
        plural="papers",
        slug_format="{citekey}",
    )
    doc = SurfacesDoc(schema_version=2, surfaces=[surface_def], path=Path("<test>"))
    filed = file_surface(
        surface_name="paper",
        title="ISM structure of galaxies",
        body="",
        sources=[],
        wiki_root=tmp_path,
        surfaces_doc=doc,
        extra_frontmatter={"citekey": "smith2024ism", "title": "ISM structure of galaxies"},
    )
    assert filed.path.name == "smith2024ism.md"


def test_file_surface_slug_format_fallback_when_missing_key(tmp_path):
    from lore_core.surfaces import SurfaceDef, SurfacesDoc
    from lore_curator.surface_filer import file_surface
    from pathlib import Path
    surface_def = SurfaceDef(
        name="paper",
        description="",
        required=["type", "citekey", "created", "description"],
        optional=[],
        slug_format="{citekey}",
    )
    doc = SurfacesDoc(schema_version=2, surfaces=[surface_def], path=Path("<test>"))
    filed = file_surface(
        surface_name="paper",
        title="Untitled paper",
        body="",
        sources=[],
        wiki_root=tmp_path,
        surfaces_doc=doc,
        extra_frontmatter={"citekey": "fallback-key"},
    )
    assert filed.path.name == "fallback-key.md"


def test_file_surface_no_slug_format_uses_title_slug(tmp_path):
    from lore_core.surfaces import SurfaceDef, SurfacesDoc
    from lore_curator.surface_filer import file_surface
    from pathlib import Path
    surface_def = SurfaceDef(
        name="concept",
        description="",
        required=["type", "created", "description"],
        optional=[],
    )
    doc = SurfacesDoc(schema_version=2, surfaces=[surface_def], path=Path("<test>"))
    filed = file_surface(
        surface_name="concept",
        title="Hot Load Standing Waves",
        body="",
        sources=[],
        wiki_root=tmp_path,
        surfaces_doc=doc,
    )
    assert filed.path.name == "hot-load-standing-waves.md"
