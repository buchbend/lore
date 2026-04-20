"""Tests for MCP surface-authoring tools."""
from __future__ import annotations

from pathlib import Path


def test_surface_context_fresh_wiki(tmp_path, monkeypatch):
    """Fresh wiki — no SURFACES.md, no notes — returns empty collections + templates."""
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    (tmp_path / "wiki" / "science").mkdir(parents=True)
    from lore_mcp.server import handle_surface_context
    ctx = handle_surface_context(wiki="science")
    assert ctx["schema"] == "lore.surface.context/1"
    assert ctx["wiki"] == "science"
    assert ctx["surfaces_md_exists"] is False
    assert ctx["current_surfaces"] == []
    assert ctx["note_samples"] == {}
    assert "standard" in ctx["shipped_templates"]
    assert "schema_version: 2" in ctx["shipped_templates"]["standard"]


def test_surface_context_with_existing_surfaces_and_notes(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    wiki_dir = tmp_path / "wiki" / "science"
    wiki_dir.mkdir(parents=True)
    (wiki_dir / "SURFACES.md").write_text(
        "# Surfaces\nschema_version: 2\n\n## concept\nA concept.\n\n```yaml\nrequired: [type, created]\noptional: []\n```\n"
    )
    concepts_dir = wiki_dir / "concepts"
    concepts_dir.mkdir()
    (concepts_dir / "2026-04-01-alpha.md").write_text(
        "---\ntype: concept\ncreated: 2026-04-01\ndescription: Alpha\n---\nbody\n"
    )
    (concepts_dir / "2026-04-02-beta.md").write_text(
        "---\ntype: concept\ncreated: 2026-04-02\ndescription: Beta\n---\nbody\n"
    )
    from lore_mcp.server import handle_surface_context
    ctx = handle_surface_context(wiki="science")
    assert ctx["surfaces_md_exists"] is True
    assert len(ctx["current_surfaces"]) == 1
    assert ctx["current_surfaces"][0]["name"] == "concept"
    assert ctx["note_samples"]["concept"][0].endswith("2026-04-02-beta]]")
