from __future__ import annotations

import textwrap
import warnings
from pathlib import Path

import pytest

from lore_core.surfaces import (
    SurfaceDef,
    SurfacesDoc,
    load_surfaces,
    load_surfaces_or_default,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MINIMAL_MD = textwrap.dedent("""\
    # Surfaces
    schema_version: 2

    ## concept
    Cross-cutting idea or pattern across sessions.

    ```yaml
    required: [type, created]
    optional: [aliases]
    ```

    Extract when: pattern appears across 3+ session notes.

    ## decision
    A trade-off made.

    ```yaml
    required: [type, created, last_reviewed, description, tags]
    optional: [superseded_by, implements]
    ```

    ## paper
    Citekey-named publication note.

    ```yaml
    required: [type, citekey, title, authors, year, description, tags]
    ```
""")


def write_surfaces(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "SURFACES.md"
    p.write_text(content)
    return p


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_load_returns_none_on_missing_file(tmp_path: Path) -> None:
    result = load_surfaces(tmp_path)
    assert result is None


def test_load_default_returns_standard_when_missing(tmp_path: Path) -> None:
    doc = load_surfaces_or_default(tmp_path)
    assert isinstance(doc, SurfacesDoc)
    names = [s.name for s in doc.surfaces]
    assert "concept" in names
    assert "decision" in names
    assert "session" in names


def test_load_parses_top_level_schema_version(tmp_path: Path) -> None:
    write_surfaces(tmp_path, MINIMAL_MD)
    doc = load_surfaces(tmp_path)
    assert doc is not None
    assert doc.schema_version == 2

    # Default to 1 when absent
    no_version = "## only\nsome prose\n"
    write_surfaces(tmp_path, no_version)
    doc2 = load_surfaces(tmp_path)
    assert doc2 is not None
    assert doc2.schema_version == 1


def test_load_parses_multiple_sections_in_order(tmp_path: Path) -> None:
    write_surfaces(tmp_path, MINIMAL_MD)
    doc = load_surfaces(tmp_path)
    assert doc is not None
    names = [s.name for s in doc.surfaces]
    assert names == ["concept", "decision", "paper"]


def test_load_extracts_required_optional_lists_from_yaml_block(tmp_path: Path) -> None:
    content = textwrap.dedent("""\
        ## mysurface
        Some description.

        ```yaml
        required: [a, b]
        optional: [c]
        ```
    """)
    write_surfaces(tmp_path, content)
    doc = load_surfaces(tmp_path)
    assert doc is not None
    assert len(doc.surfaces) == 1
    sd = doc.surfaces[0]
    assert sd.required == ["a", "b"]
    assert sd.optional == ["c"]


def test_load_extracts_description_prose_above_yaml(tmp_path: Path) -> None:
    content = textwrap.dedent("""\
        ## mysurface
        Cross-cutting idea or pattern.

        ```yaml
        required: [type]
        ```
    """)
    write_surfaces(tmp_path, content)
    doc = load_surfaces(tmp_path)
    assert doc is not None
    sd = doc.surfaces[0]
    assert sd.description == "Cross-cutting idea or pattern."


def test_load_extracts_extract_when_line_below_yaml(tmp_path: Path) -> None:
    content = textwrap.dedent("""\
        ## concept
        Cross-cutting idea or pattern across sessions.

        ```yaml
        required: [type, created]
        ```

        Extract when: pattern appears across 3+ session notes
    """)
    write_surfaces(tmp_path, content)
    doc = load_surfaces(tmp_path)
    assert doc is not None
    sd = doc.surfaces[0]
    assert sd.extract_when == "pattern appears across 3+ session notes"


def test_load_unknown_yaml_key_warns_but_loads(tmp_path: Path) -> None:
    content = textwrap.dedent("""\
        ## mysurface
        A description.

        ```yaml
        required: [a]
        bogus: 42
        ```
    """)
    write_surfaces(tmp_path, content)
    with pytest.warns(UserWarning, match="unknown YAML key 'bogus'"):
        doc = load_surfaces(tmp_path)
    assert doc is not None
    assert len(doc.surfaces) == 1
    sd = doc.surfaces[0]
    assert sd.required == ["a"]


def test_load_malformed_yaml_block_warns_and_skips_section(tmp_path: Path) -> None:
    content = textwrap.dedent("""\
        ## good
        Good section.

        ```yaml
        required: [x]
        ```

        ## bad
        Bad YAML below.

        ```yaml
        required: [a, b
        ```
    """)
    write_surfaces(tmp_path, content)
    with pytest.warns(UserWarning):
        doc = load_surfaces(tmp_path)
    assert doc is not None
    names = [s.name for s in doc.surfaces]
    assert "good" in names
    assert "bad" not in names


def test_load_extract_when_optional_defaults_empty_string(tmp_path: Path) -> None:
    content = textwrap.dedent("""\
        ## mysurface
        Description only.

        ```yaml
        required: [type]
        ```
    """)
    write_surfaces(tmp_path, content)
    doc = load_surfaces(tmp_path)
    assert doc is not None
    sd = doc.surfaces[0]
    assert sd.extract_when == ""


def test_load_no_yaml_block_section_still_parses_with_empty_required(tmp_path: Path) -> None:
    content = textwrap.dedent("""\
        ## plain
        Just prose, no YAML block at all.
    """)
    write_surfaces(tmp_path, content)
    doc = load_surfaces(tmp_path)
    assert doc is not None
    assert len(doc.surfaces) == 1
    sd = doc.surfaces[0]
    assert sd.required == []
    assert sd.optional == []
    assert sd.description == "Just prose, no YAML block at all."


def test_surfaces_parse_plural_key():
    text = (
        "# Surfaces\nschema_version: 2\n\n"
        "## paper\nA paper.\n\n"
        "```yaml\nrequired: [type]\noptional: []\nplural: papers\n```\n"
    )
    from lore_core.surfaces import _parse
    from pathlib import Path
    doc = _parse(text, Path("<test>"))
    assert doc.surfaces[0].plural == "papers"


def test_surfaces_parse_slug_format_key():
    text = (
        "# Surfaces\nschema_version: 2\n\n"
        "## paper\nA paper.\n\n"
        "```yaml\nrequired: [type, citekey]\noptional: []\nslug_format: \"{citekey}\"\n```\n"
    )
    from lore_core.surfaces import _parse
    from pathlib import Path
    doc = _parse(text, Path("<test>"))
    assert doc.surfaces[0].slug_format == "{citekey}"


def test_surfaces_parse_extract_prompt_key():
    text = (
        "# Surfaces\nschema_version: 2\n\n"
        "## paper\nA paper.\n\n"
        "```yaml\nrequired: [type]\noptional: []\nextract_prompt: \"Prefer citekey.\"\n```\n"
    )
    from lore_core.surfaces import _parse
    from pathlib import Path
    doc = _parse(text, Path("<test>"))
    assert doc.surfaces[0].extract_prompt == "Prefer citekey."


def test_surfaces_parse_new_keys_absent_defaults_to_none():
    text = (
        "# Surfaces\nschema_version: 2\n\n"
        "## concept\nA concept.\n\n"
        "```yaml\nrequired: [type]\noptional: []\n```\n"
    )
    from lore_core.surfaces import _parse
    from pathlib import Path
    s = _parse(text, Path("<test>")).surfaces[0]
    assert s.plural is None
    assert s.slug_format is None
    assert s.extract_prompt is None


def test_render_section_minimal():
    from lore_core.surfaces import SurfaceDef, render_section
    s = SurfaceDef(
        name="concept",
        description="Cross-cutting idea.",
        required=["type", "created"],
        optional=["draft"],
    )
    out = render_section(s)
    assert out.startswith("## concept\n")
    assert "Cross-cutting idea." in out
    assert "required: [type, created]" in out
    assert "optional: [draft]" in out
    assert out.endswith("\n")


def test_render_section_with_new_keys():
    from lore_core.surfaces import SurfaceDef, render_section
    s = SurfaceDef(
        name="paper",
        description="Publication.",
        required=["type", "citekey"],
        optional=[],
        extract_when="paper discussed",
        plural="papers",
        slug_format="{citekey}",
        extract_prompt="Prefer citekey.",
    )
    out = render_section(s)
    assert "plural: papers" in out
    assert 'slug_format: "{citekey}"' in out or "slug_format: '{citekey}'" in out
    assert "extract_prompt: " in out
    assert "Extract when: paper discussed" in out


def test_render_section_roundtrips_through_parser():
    from lore_core.surfaces import SurfaceDef, render_section, _parse
    from pathlib import Path
    s = SurfaceDef(
        name="paper",
        description="A publication.",
        required=["type", "citekey", "title"],
        optional=["draft"],
        plural="papers",
        slug_format="{citekey}",
        extract_prompt="Use citekey.",
    )
    section = render_section(s)
    doc_text = f"# Surfaces\nschema_version: 2\n\n{section}"
    parsed = _parse(doc_text, Path("<test>")).surfaces[0]
    assert parsed.name == "paper"
    assert parsed.required == ["type", "citekey", "title"]
    assert parsed.plural == "papers"
    assert parsed.slug_format == "{citekey}"
    assert parsed.extract_prompt == "Use citekey."


def test_render_document_with_header_and_sections():
    from lore_core.surfaces import SurfaceDef, render_document
    a = SurfaceDef(name="concept", description="A.", required=["type"], optional=[])
    b = SurfaceDef(name="decision", description="B.", required=["type"], optional=[])
    out = render_document(schema_version=2, surfaces=[a, b], wiki="science")
    assert out.startswith("# Surfaces — science\n")
    assert "schema_version: 2" in out
    assert "## concept" in out
    assert "## decision" in out


def test_render_section_escapes_backslash_in_slug_format():
    from lore_core.surfaces import SurfaceDef, render_section, _parse
    from pathlib import Path
    s = SurfaceDef(
        name="paper",
        description="A.",
        required=["type", "citekey"],
        optional=[],
        slug_format="{citekey}\\extra",
    )
    section = render_section(s)
    doc_text = f"# Surfaces\nschema_version: 2\n\n{section}"
    parsed = _parse(doc_text, Path("<test>")).surfaces[0]
    assert parsed.slug_format == "{citekey}\\extra"


def test_render_section_escapes_backslash_in_extract_prompt():
    from lore_core.surfaces import SurfaceDef, render_section, _parse
    from pathlib import Path
    s = SurfaceDef(
        name="paper",
        description="A.",
        required=["type"],
        optional=[],
        extract_prompt="Use slash (/), not backslash (\\).",
    )
    section = render_section(s)
    doc_text = f"# Surfaces\nschema_version: 2\n\n{section}"
    parsed = _parse(doc_text, Path("<test>")).surfaces[0]
    assert parsed.extract_prompt == "Use slash (/), not backslash (\\)."


def test_render_section_multiline_extract_prompt_roundtrips():
    from lore_core.surfaces import SurfaceDef, render_section, _parse
    from pathlib import Path
    s = SurfaceDef(
        name="paper",
        description="A publication.",
        required=["type"],
        optional=[],
        extract_prompt="Line one.\nLine two.\nLine three.",
    )
    section = render_section(s)
    doc_text = f"# Surfaces\nschema_version: 2\n\n{section}"
    parsed = _parse(doc_text, Path("<test>")).surfaces[0]
    assert parsed.extract_prompt == "Line one.\nLine two.\nLine three."


# ---------------------------------------------------------------------------
# validate_draft tests
# ---------------------------------------------------------------------------

from lore_core.surfaces import validate_draft  # noqa: E402


def _minimal_append_draft(**overrides):
    base = {
        "schema": "lore.surface.draft/1",
        "wiki": "x",
        "operation": "append",
        "surface": {
            "name": "paper",
            "description": "A paper.",
            "required": ["type", "created", "description", "tags"],
            "optional": ["draft"],
        },
    }
    base["surface"].update(overrides)
    return base


def test_validate_draft_happy_path_append(tmp_path):
    (tmp_path / "SURFACES.md").write_text(
        "# Surfaces\nschema_version: 2\n\n## concept\nA.\n\n```yaml\nrequired: [type]\noptional: []\n```\n"
    )
    issues = validate_draft(_minimal_append_draft(), wiki_dir=tmp_path)
    assert issues == []


def test_validate_draft_rejects_duplicate_name(tmp_path):
    (tmp_path / "SURFACES.md").write_text(
        "# Surfaces\nschema_version: 2\n\n## paper\nA.\n\n```yaml\nrequired: [type]\noptional: []\n```\n"
    )
    issues = validate_draft(_minimal_append_draft(), wiki_dir=tmp_path)
    assert any(i["code"] == "duplicate_name" for i in issues)


def test_validate_draft_rejects_required_optional_overlap(tmp_path):
    d = _minimal_append_draft(required=["type", "draft"], optional=["draft"])
    issues = validate_draft(d, wiki_dir=tmp_path)
    assert any(i["code"] == "required_optional_overlap" for i in issues)


def test_validate_draft_rejects_bad_name_shape(tmp_path):
    d = _minimal_append_draft(name="My Fancy Surface!")
    issues = validate_draft(d, wiki_dir=tmp_path)
    assert any(i["code"] == "invalid_name" for i in issues)


def test_validate_draft_rejects_bad_plural_shape(tmp_path):
    d = _minimal_append_draft(plural="Papers Galore!")
    issues = validate_draft(d, wiki_dir=tmp_path)
    assert any(i["code"] == "invalid_plural" for i in issues)


def test_validate_draft_rejects_plural_collision(tmp_path):
    (tmp_path / "SURFACES.md").write_text(
        "# Surfaces\nschema_version: 2\n\n## paper\nA.\n\n```yaml\nrequired: [type]\noptional: []\n```\n"
    )
    d = _minimal_append_draft(name="study", plural="papers")
    issues = validate_draft(d, wiki_dir=tmp_path)
    assert any(i["code"] == "plural_collision" for i in issues)


def test_validate_draft_rejects_unknown_slug_format_placeholder(tmp_path):
    d = _minimal_append_draft(slug_format="{nonsense}")
    issues = validate_draft(d, wiki_dir=tmp_path)
    assert any(i["code"] == "invalid_slug_format" for i in issues)


def test_validate_draft_accepts_known_slug_format_placeholders(tmp_path):
    d = _minimal_append_draft(
        required=["type", "created", "description", "tags", "citekey"],
        slug_format="{citekey}-{date}",
    )
    issues = validate_draft(d, wiki_dir=tmp_path)
    assert issues == []


def test_validate_draft_rejects_empty_extract_prompt(tmp_path):
    d = _minimal_append_draft(extract_prompt="")
    issues = validate_draft(d, wiki_dir=tmp_path)
    assert any(i["code"] == "empty_extract_prompt" for i in issues)


def test_validate_draft_init_operation(tmp_path):
    d = {
        "schema": "lore.surface.draft/1",
        "wiki": "x",
        "operation": "init",
        "schema_version": 2,
        "surfaces": [
            {"name": "a", "description": "A", "required": ["type"], "optional": []},
            {"name": "b", "description": "B", "required": ["type"], "optional": []},
        ],
    }
    issues = validate_draft(d, wiki_dir=tmp_path)
    assert issues == []


def test_load_surfaces_or_default_reads_packaged_standard(tmp_path):
    from lore_core.surfaces import load_surfaces_or_default
    doc = load_surfaces_or_default(tmp_path)  # no SURFACES.md → fallback
    names = [s.name for s in doc.surfaces]
    assert names == ["concept", "decision", "session"]
    assert doc.schema_version == 2


def test_load_surfaces_or_default_cache_returns_same_object(tmp_path):
    from lore_core.surfaces import load_surfaces_or_default
    a = load_surfaces_or_default(tmp_path)
    b = load_surfaces_or_default(tmp_path)
    assert [s.name for s in a.surfaces] == [s.name for s in b.surfaces]


def test_validate_draft_init_detects_internal_collision(tmp_path):
    d = {
        "schema": "lore.surface.draft/1",
        "wiki": "x",
        "operation": "init",
        "schema_version": 2,
        "surfaces": [
            {"name": "concept", "description": "", "required": ["type"], "optional": []},
            {"name": "concept", "description": "", "required": ["type"], "optional": []},
        ],
    }
    issues = validate_draft(d, wiki_dir=tmp_path)
    assert any(i["code"] == "duplicate_name" for i in issues)
