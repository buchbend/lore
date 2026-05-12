"""Tests for handle_read default-redact behaviour (issue #93)."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from lore_core.regions import HUMAN_ONLY_MARKER
from lore_mcp.server import handle_read


def _setup_wiki(
    tmp_path: Path,
    monkeypatch,
    *,
    body: str,
    name: str = "note.md",
) -> Path:
    wiki = tmp_path / "wiki" / "demo"
    (wiki / "concepts").mkdir(parents=True)
    (wiki / "concepts" / name).write_text(body)
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    return wiki


def test_default_read_redacts_human_only(tmp_path, monkeypatch):
    body = dedent(f"""\
        # Title

        Reload-safe content here.

        {HUMAN_ONLY_MARKER}
        Private scratch — should never reach the LLM.
        """)
    _setup_wiki(tmp_path, monkeypatch, body=body)
    result = handle_read("concepts/note.md", wiki="demo")
    assert "error" not in result
    assert "Reload-safe content here." in result["content"]
    assert "Private scratch" not in result["content"]
    assert HUMAN_ONLY_MARKER not in result["content"]


def test_include_human_true_returns_full_note(tmp_path, monkeypatch):
    body = dedent(f"""\
        # Title

        Reload-safe content here.

        {HUMAN_ONLY_MARKER}
        Private scratch — visible only when include_human=true.
        """)
    _setup_wiki(tmp_path, monkeypatch, body=body)
    result = handle_read("concepts/note.md", wiki="demo", include_human=True)
    assert "error" not in result
    assert result["content"] == body
    assert "Private scratch" in result["content"]
    assert HUMAN_ONLY_MARKER in result["content"]


def test_old_note_without_marker_returned_in_full_default(tmp_path, monkeypatch):
    """Backwards compatibility: notes pre-dating the two-region design have
    no marker and must round-trip unchanged through the default filter."""
    body = "# Old Note\n\nNo marker, all content is reload-safe.\n"
    _setup_wiki(tmp_path, monkeypatch, body=body)
    result = handle_read("concepts/note.md", wiki="demo")
    assert "error" not in result
    assert result["content"] == body


def test_old_note_without_marker_returned_in_full_with_include_human(
    tmp_path, monkeypatch
):
    body = "# Old Note\n\nNo marker anywhere.\n"
    _setup_wiki(tmp_path, monkeypatch, body=body)
    result = handle_read(
        "concepts/note.md", wiki="demo", include_human=True
    )
    assert "error" not in result
    assert result["content"] == body


def test_section_arg_works_against_redacted_text(tmp_path, monkeypatch):
    """When `section` is requested on a note with both regions, the H2
    lookup runs against the *redacted* text — a section that lives only
    in the human-only region must be invisible to the default reader."""
    body = dedent(f"""\
        # Title

        ## Public

        public body

        {HUMAN_ONLY_MARKER}
        ## Private

        private body
        """)
    _setup_wiki(tmp_path, monkeypatch, body=body)
    result = handle_read(
        "concepts/note.md", wiki="demo", section="public"
    )
    assert "error" not in result
    assert "public body" in result["content"]

    # The Private section must NOT be retrievable by default.
    missed = handle_read(
        "concepts/note.md", wiki="demo", section="private"
    )
    assert "error" in missed
    assert missed["error"]["code"] == "section_not_found"

    # …but include_human=true makes it visible.
    found = handle_read(
        "concepts/note.md",
        wiki="demo",
        section="private",
        include_human=True,
    )
    assert "error" not in found
    assert "private body" in found["content"]


def test_marker_inside_code_fence_is_not_a_boundary(tmp_path, monkeypatch):
    """A marker that lives inside a fenced code block (e.g. documenting
    the marker in a note about Lore itself) does NOT redact anything."""
    body = dedent(f"""\
        # About the marker

        The marker literal is:

        ```markdown
        {HUMAN_ONLY_MARKER}
        ```

        And this paragraph is still reload-safe.
        """)
    _setup_wiki(tmp_path, monkeypatch, body=body)
    result = handle_read("concepts/note.md", wiki="demo")
    assert "error" not in result
    assert result["content"] == body
    assert "still reload-safe" in result["content"]
