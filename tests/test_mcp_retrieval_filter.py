"""Two-region retrieval filter at MCP boundaries (issue #96).

PRD #92 defines a uniform retrieval contract: LLM-facing surfaces (the
ones the agent loads autonomously) strip the ``<!-- lore:human-only -->``
region; user-invoked surfaces return the full note.

Slice #93 wired the contract for ``handle_read`` and the regions
primitives. Slice #96 extends it to the remaining LLM-facing handlers
(``handle_search``, ``handle_surface_context``, ``handle_briefing_gather``)
and asserts that the user-invoked surfaces (``handle_resume``,
``handle_drill``) stay un-redacted.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
from lore_core.regions import HUMAN_ONLY_MARKER


HUMAN_ONLY_NEEDLE = "PRIVATE-NEEDLE-DO-NOT-LEAK"
RELOAD_SAFE_NEEDLE = "public-needle-may-leak"


@pytest.fixture(autouse=True)
def _reset_reindex_throttle():
    """Each test pivots ``$LORE_CACHE`` to a fresh tmp_path so the FTS
    database starts empty. The MCP server keeps a module-global
    reindex throttle keyed by wiki name; without resetting it, a second
    test using the same wiki name would skip reindex and search against
    an empty index. Clear the throttle for every test in this file."""
    from lore_mcp import server

    server._reindex_last_seen.clear()
    yield
    server._reindex_last_seen.clear()


def _flatten(value) -> str:
    """Recursively flatten a JSON-ish value to one big string for needle hunting."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_flatten(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_flatten(v) for v in value)
    return repr(value)


# ---------------------------------------------------------------------------
# handle_search
# ---------------------------------------------------------------------------


@pytest.fixture
def search_vault(tmp_path, monkeypatch):
    """One note with both regions; reload-safe carries the search term."""
    vault_root = tmp_path / "vault"
    wiki = vault_root / "wiki" / "demo"
    (wiki / "concepts").mkdir(parents=True)
    (wiki / "concepts" / "two-region.md").write_text(dedent(f"""\
        ---
        schema_version: 2
        type: concept
        description: "A two-region demo note"
        tags: [demo]
        ---
        # Two-region demo

        Reload-safe body mentions {RELOAD_SAFE_NEEDLE}.

        {HUMAN_ONLY_MARKER}
        Human-only body mentions {HUMAN_ONLY_NEEDLE}.
        """))
    monkeypatch.setenv("LORE_ROOT", str(vault_root))
    monkeypatch.setenv("LORE_CACHE", str(tmp_path / "cache"))
    return vault_root


def test_search_response_omits_human_only_content(search_vault):
    """handle_search's response carries no body content, so any term that
    only lives in the human-only region must not leak via any response
    field (description, tags, snippet, …)."""
    from lore_mcp.server import handle_search

    hits = handle_search(query=RELOAD_SAFE_NEEDLE, wiki="demo")
    assert hits, "reload-safe term should still surface the note"
    blob = _flatten(hits)
    assert HUMAN_ONLY_NEEDLE not in blob
    assert HUMAN_ONLY_MARKER not in blob


# ---------------------------------------------------------------------------
# handle_surface_context
# ---------------------------------------------------------------------------


def test_surface_context_redacts_claude_md_attach(tmp_path, monkeypatch):
    """The ``## Lore`` attach block from CLAUDE.md is LLM-facing — a
    marker inside it must be stripped before the dict is returned."""
    from lore_mcp.server import handle_surface_context

    vault_root = tmp_path / "vault"
    wiki = vault_root / "wiki" / "demo"
    wiki.mkdir(parents=True)
    (wiki / "CLAUDE.md").write_text(dedent(f"""\
        # Project

        ## Lore

        Reload-safe orientation mentions {RELOAD_SAFE_NEEDLE}.

        {HUMAN_ONLY_MARKER}
        Human-only mentions {HUMAN_ONLY_NEEDLE}.
        """))
    monkeypatch.setenv("LORE_ROOT", str(vault_root))

    result = handle_surface_context(wiki="demo")
    attach = result["claude_md_attach"]
    assert RELOAD_SAFE_NEEDLE in attach
    assert HUMAN_ONLY_NEEDLE not in attach
    assert HUMAN_ONLY_MARKER not in attach


# ---------------------------------------------------------------------------
# handle_briefing_gather
# ---------------------------------------------------------------------------


def test_briefing_gather_redacts_session_sections(tmp_path, monkeypatch):
    """Sections that live in the human-only region of a session note must
    NOT appear in the gather output (the gather feeds an LLM compose)."""
    from lore_mcp.server import handle_briefing_gather

    vault_root = tmp_path / "vault"
    wiki = vault_root / "wiki" / "demo"
    (wiki / "sessions").mkdir(parents=True)
    (wiki / "sessions" / "2026-05-12-demo.md").write_text(dedent(f"""\
        ---
        schema_version: 2
        type: session
        created: 2026-05-12
        description: "demo session"
        ---

        ## Public

        Reload-safe body mentions {RELOAD_SAFE_NEEDLE}.

        {HUMAN_ONLY_MARKER}
        ## Private

        Human-only body mentions {HUMAN_ONLY_NEEDLE}.
        """))
    monkeypatch.setenv("LORE_ROOT", str(vault_root))

    result = handle_briefing_gather(wiki="demo")
    assert "error" not in result
    sessions = result["new_sessions"]
    assert len(sessions) == 1
    sections = sessions[0]["sections"]

    # Reload-safe section survives; human-only section is gone entirely.
    assert "public" in sections
    assert RELOAD_SAFE_NEEDLE in sections["public"]
    assert "private" not in sections
    blob = _flatten(result)
    assert HUMAN_ONLY_NEEDLE not in blob
    assert HUMAN_ONLY_MARKER not in blob


# ---------------------------------------------------------------------------
# Negative: user-invoked surfaces return the full note
# ---------------------------------------------------------------------------


def test_drill_returns_full_content_including_human_only(tmp_path, monkeypatch):
    """``handle_drill`` is the user-invoked deep-dive surface — it must
    return both regions so the user (or skill they explicitly invoked)
    sees their own thinking."""
    from lore_mcp.server import handle_drill

    vault_root = tmp_path / "vault"
    wiki = vault_root / "wiki" / "demo"
    (wiki / "concepts").mkdir(parents=True)
    (wiki / "concepts" / "drillable.md").write_text(dedent(f"""\
        ---
        schema_version: 2
        type: concept
        description: "drillable note"
        tags: [drill]
        ---
        # Drillable

        Reload-safe body mentions {RELOAD_SAFE_NEEDLE}.

        {HUMAN_ONLY_MARKER}
        Human-only body mentions {HUMAN_ONLY_NEEDLE}.
        """))
    monkeypatch.setenv("LORE_ROOT", str(vault_root))
    monkeypatch.setenv("LORE_CACHE", str(tmp_path / "cache"))

    out = handle_drill(query=RELOAD_SAFE_NEEDLE, wiki="demo")
    assert "error" not in out
    notes = out["result"]["notes"]
    assert notes, "drill must surface the matching note"
    contents = "\n".join(n.get("content", "") for n in notes)
    assert RELOAD_SAFE_NEEDLE in contents
    assert HUMAN_ONLY_NEEDLE in contents, (
        "drill is user-invoked per PRD #92; the human-only region must "
        "round-trip in full"
    )


def test_resume_keyword_mode_passes_through_descriptions(tmp_path, monkeypatch):
    """``handle_resume`` is user-invoked. Frontmatter ``description``
    never carries human-only content (the marker is body-only), so the
    keyword-mode descriptions surface unchanged."""
    from lore_mcp.server import handle_resume

    vault_root = tmp_path / "vault"
    wiki = vault_root / "wiki" / "demo"
    (wiki / "concepts").mkdir(parents=True)
    (wiki / "concepts" / "resumable.md").write_text(dedent(f"""\
        ---
        schema_version: 2
        type: concept
        description: "resumable note"
        tags: [resume]
        ---
        # Resumable

        Reload-safe body mentions {RELOAD_SAFE_NEEDLE}.

        {HUMAN_ONLY_MARKER}
        Human-only body mentions {HUMAN_ONLY_NEEDLE}.
        """))
    monkeypatch.setenv("LORE_ROOT", str(vault_root))
    monkeypatch.setenv("LORE_CACHE", str(tmp_path / "cache"))

    out = handle_resume(keyword=RELOAD_SAFE_NEEDLE, wiki="demo")
    assert "error" not in out
    # Resume's keyword mode returns search-shaped hits (description from
    # frontmatter, no body). The point of this test is that resume is
    # NOT in the redact set — i.e. it doesn't error or strip the legitimate
    # frontmatter description.
    blob = _flatten(out)
    assert "resumable note" in blob
