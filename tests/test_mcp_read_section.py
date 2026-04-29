"""Tests for the optional `section` arg on lore_read (Phase 3.2)."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from lore_mcp.server import _extract_section, handle_read


def _setup_wiki(tmp_path: Path, monkeypatch, *, body: str, name: str = "note.md") -> Path:
    wiki = tmp_path / "wiki" / "demo"
    (wiki / "concepts").mkdir(parents=True)
    (wiki / "concepts" / name).write_text(body)
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    return wiki


def test_no_section_arg_returns_whole_file(tmp_path, monkeypatch):
    body = "# Title\n\n## A\nalpha\n\n## B\nbeta\n"
    _setup_wiki(tmp_path, monkeypatch, body=body)
    result = handle_read("concepts/note.md", wiki="demo")
    assert "error" not in result
    assert result["content"] == body
    assert "section" not in result


def test_section_returns_just_that_section(tmp_path, monkeypatch):
    body = dedent("""\
        # Title

        ## Alpha
        body of alpha

        ## Beta
        body of beta
        """)
    _setup_wiki(tmp_path, monkeypatch, body=body)
    result = handle_read("concepts/note.md", wiki="demo", section="alpha")
    assert "error" not in result
    assert result["content"].startswith("## Alpha")
    assert "body of alpha" in result["content"]
    assert "body of beta" not in result["content"]
    assert result["section"] == "alpha"


def test_section_includes_nested_h3_subsections(tmp_path, monkeypatch):
    body = dedent("""\
        ## Outer
        outer body

        ### Nested
        nested body

        ## Sibling
        sibling body
        """)
    _setup_wiki(tmp_path, monkeypatch, body=body)
    result = handle_read("concepts/note.md", wiki="demo", section="outer")
    assert "### Nested" in result["content"]
    assert "nested body" in result["content"]
    assert "Sibling" not in result["content"]


def test_section_case_insensitive_substring_match(tmp_path, monkeypatch):
    body = "## Decisions Considered\nfoo\n## Open Questions\nbar\n"
    _setup_wiki(tmp_path, monkeypatch, body=body)
    # First-match-wins by document order.
    result = handle_read("concepts/note.md", wiki="demo", section="decisions")
    assert "Decisions Considered" in result["content"]
    assert "Open Questions" not in result["content"]


def test_section_skips_code_fenced_h2(tmp_path, monkeypatch):
    """`## not a heading` inside a fenced code block is NOT a section boundary."""
    body = dedent("""\
        ## Real Section
        real body

        ```python
        # Example markdown processing:
        ## not a heading inside a fence
        x = 1
        ```

        more real body after the fence
        """)
    _setup_wiki(tmp_path, monkeypatch, body=body)
    result = handle_read("concepts/note.md", wiki="demo", section="real")
    # The whole section, including the code block AND the post-fence text,
    # must be returned (because the fenced ## was correctly ignored).
    assert "real body" in result["content"]
    assert "```python" in result["content"]
    assert "more real body after the fence" in result["content"]


def test_section_no_h2_in_note_returns_dedicated_error(tmp_path, monkeypatch):
    body = "# Title only\n\nplain body, no H2\n"
    _setup_wiki(tmp_path, monkeypatch, body=body)
    result = handle_read("concepts/note.md", wiki="demo", section="anything")
    assert "error" in result
    assert result["error"]["code"] == "section_not_found"
    assert "no H2 sections" in result["error"]["message"]


def test_section_no_match_lists_available_headings(tmp_path, monkeypatch):
    body = "## Alpha\n\n## Beta\n"
    _setup_wiki(tmp_path, monkeypatch, body=body)
    result = handle_read("concepts/note.md", wiki="demo", section="zeta")
    assert "error" in result
    assert result["error"]["code"] == "section_not_found"
    assert "Alpha" in result["error"]["next"]
    assert "Beta" in result["error"]["next"]


def test_extract_section_returns_h2_inventory_on_no_match():
    body = "## Alpha\nfoo\n## Beta\nbar\n"
    section_text, headings = _extract_section(body, "missing")
    assert section_text is None
    assert headings == ["Alpha", "Beta"]


def test_extract_section_handles_tilde_fences():
    body = dedent("""\
        ## Real
        body

        ~~~
        ## fenced (tilde) — must not be a section
        ~~~

        more body
        """)
    section_text, _ = _extract_section(body, "real")
    assert section_text is not None
    assert "more body" in section_text


def test_extract_section_fences_dont_cross_terminate():
    """A ``` fence opener is not closed by a ~~~ closer."""
    body = dedent("""\
        ## Real
        body

        ```
        ~~~ this is inside a backtick fence
        ## still inside the fence
        ```

        ## Other
        other body
        """)
    section_text, _ = _extract_section(body, "real")
    assert section_text is not None
    # "## still inside the fence" must NOT terminate the Real section.
    assert "## still inside the fence" in section_text
    assert "Other" not in section_text
