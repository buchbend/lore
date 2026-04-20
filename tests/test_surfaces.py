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
