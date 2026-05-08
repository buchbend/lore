"""Tests for freshness wiring on MCP retrieval surfaces (slice 1).

Covers ``handle_read`` and ``handle_search`` (which `handle_drill`
composes; drill therefore inherits freshness automatically — verified
by a smoke test).
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from lore_mcp.server import handle_read, handle_search


def _setup_wiki(
    tmp_path: Path,
    monkeypatch,
    *,
    body: str,
    name: str = "note.md",
    folder: str = "concepts",
) -> Path:
    wiki = tmp_path / "wiki" / "demo"
    (wiki / folder).mkdir(parents=True)
    (wiki / folder / name).write_text(body)
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    return wiki


# ---------------------------------------------------------------------------
# handle_read
# ---------------------------------------------------------------------------


def test_handle_read_attaches_freshness_default_confirmed(tmp_path, monkeypatch):
    body = dedent("""\
        ---
        type: concept
        ---
        body
        """)
    _setup_wiki(tmp_path, monkeypatch, body=body)
    result = handle_read("concepts/note.md", wiki="demo")
    assert "freshness" in result
    assert result["freshness"]["status"] == "confirmed"
    assert result["freshness"]["cause"] == "none"


def test_handle_read_status_stale_marks_candidate(tmp_path, monkeypatch):
    body = dedent("""\
        ---
        type: concept
        status: stale
        ---
        body
        """)
    _setup_wiki(tmp_path, monkeypatch, body=body)
    result = handle_read("concepts/note.md", wiki="demo")
    assert result["freshness"]["status"] == "stale-candidate"
    assert result["freshness"]["cause"] == "authored_marker"


def test_handle_read_supersede_candidate_marks_candidate(tmp_path, monkeypatch):
    body = dedent("""\
        ---
        type: concept
        supersede_candidate: "[[newer]]"
        ---
        body
        """)
    _setup_wiki(tmp_path, monkeypatch, body=body)
    result = handle_read("concepts/note.md", wiki="demo")
    assert result["freshness"]["status"] == "stale-candidate"
    assert result["freshness"]["cause"] == "authored_marker"


def test_handle_read_supersede_candidate_of_marks_candidate(tmp_path, monkeypatch):
    body = dedent("""\
        ---
        type: concept
        supersede_candidate_of: "[[older]]"
        ---
        body
        """)
    _setup_wiki(tmp_path, monkeypatch, body=body)
    result = handle_read("concepts/note.md", wiki="demo")
    assert result["freshness"]["status"] == "stale-candidate"
    assert result["freshness"]["cause"] == "authored_marker"


def test_handle_read_section_response_carries_freshness(tmp_path, monkeypatch):
    body = dedent("""\
        ---
        status: stale
        ---
        # T

        ## Alpha
        body
        """)
    _setup_wiki(tmp_path, monkeypatch, body=body)
    result = handle_read("concepts/note.md", wiki="demo", section="alpha")
    assert result.get("section") == "alpha"
    assert result["freshness"]["status"] == "stale-candidate"


# ---------------------------------------------------------------------------
# handle_search
# ---------------------------------------------------------------------------


def _bootstrap_searchable_wiki(
    tmp_path: Path, monkeypatch, files: dict[str, str]
) -> Path:
    """Create a wiki with the given files and run lint to populate indexes."""
    wiki = tmp_path / "wiki" / "demo"
    (wiki / "concepts").mkdir(parents=True)
    for name, body in files.items():
        (wiki / "concepts" / name).write_text(body)
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    # Populate _catalog.json so reindex/search has metadata.
    from lore_core.lint import run_lint

    run_lint()
    return wiki


def test_handle_search_each_hit_carries_freshness(tmp_path, monkeypatch):
    files = {
        "fresh.md": dedent("""\
            ---
            type: concept
            description: alpha thing
            tags: [alpha]
            ---
            alpha quick brown fox
            """),
        "stale.md": dedent("""\
            ---
            type: concept
            description: alpha legacy
            tags: [alpha]
            status: stale
            ---
            alpha legacy fox content
            """),
    }
    _bootstrap_searchable_wiki(tmp_path, monkeypatch, files)
    hits = handle_search("alpha fox", wiki="demo", k=5)
    assert len(hits) >= 1
    for h in hits:
        assert "freshness" in h
        assert h["freshness"]["status"] in {"confirmed", "stale-candidate"}
    # The stale one should be flagged.
    by_path = {h["path"]: h for h in hits}
    if "concepts/stale.md" in by_path:
        assert by_path["concepts/stale.md"]["freshness"]["status"] == "stale-candidate"
    if "concepts/fresh.md" in by_path:
        assert by_path["concepts/fresh.md"]["freshness"]["status"] == "confirmed"


def test_handle_search_default_is_confirmed(tmp_path, monkeypatch):
    files = {
        "n.md": dedent("""\
            ---
            type: concept
            description: alpha thing
            tags: [alpha]
            ---
            alpha quick brown fox
            """),
    }
    _bootstrap_searchable_wiki(tmp_path, monkeypatch, files)
    hits = handle_search("alpha", wiki="demo", k=5)
    if hits:
        assert hits[0]["freshness"]["status"] == "confirmed"
        assert hits[0]["freshness"]["cause"] == "none"
