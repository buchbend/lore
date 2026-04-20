"""End-to-end: draft JSON → MCP validate → CLI commit → SURFACES.md on disk.

Skips the LLM / skill conversation — exercises the deterministic bottom half
of the pipeline that everything else depends on.
"""
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from lore_cli.surface_cmd import app
from lore_mcp.server import handle_surface_context, handle_surface_validate

runner = CliRunner(mix_stderr=False)


def test_flow_a_append_end_to_end(tmp_path, monkeypatch):
    """Simulate flow A: context → validate → commit → resulting SURFACES.md parses clean."""
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    wiki_dir = tmp_path / "wiki" / "science"
    wiki_dir.mkdir(parents=True)
    (wiki_dir / "SURFACES.md").write_text(
        "# Surfaces — science\nschema_version: 2\n\n"
        "## concept\nA concept.\n\n```yaml\nrequired: [type, created, description, tags]\noptional: [draft]\n```\n"
    )
    # 1. Context tool returns the wiki state
    ctx = handle_surface_context(wiki="science")
    assert [s["name"] for s in ctx["current_surfaces"]] == ["concept"]

    # 2. Build a draft
    draft = {
        "schema": "lore.surface.draft/1",
        "wiki": "science",
        "operation": "append",
        "surface": {
            "name": "paper",
            "description": "Citekey-named publication note.",
            "required": ["type", "created", "description", "tags", "citekey"],
            "optional": ["draft"],
            "extract_when": "a paper is discussed with concrete findings",
            "plural": "papers",
            "slug_format": "{citekey}",
            "extract_prompt": "Prefer citekey over title for slug.",
        },
    }

    # 3. Validate tool says OK
    result = handle_surface_validate(wiki="science", draft=draft)
    assert result["ok"] is True, result["issues"]

    # 4. Commit via CLI
    draft_path = tmp_path / "draft.json"
    draft_path.write_text(json.dumps(draft))
    cli = runner.invoke(app, ["commit", str(draft_path)])
    assert cli.exit_code == 0, cli.output + cli.stderr

    # 5. Resulting SURFACES.md parses cleanly and contains both surfaces
    from lore_core.surfaces import load_surfaces
    doc = load_surfaces(wiki_dir)
    assert doc is not None
    assert [s.name for s in doc.surfaces] == ["concept", "paper"]
    paper = doc.surfaces[1]
    assert paper.plural == "papers"
    assert paper.slug_format == "{citekey}"
    assert "citekey" in paper.extract_prompt


def test_flow_b_init_end_to_end(tmp_path, monkeypatch):
    """Simulate flow B: validate → commit → fresh SURFACES.md parses clean."""
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    (tmp_path / "wiki" / "science").mkdir(parents=True)
    draft = {
        "schema": "lore.surface.draft/1",
        "wiki": "science",
        "operation": "init",
        "schema_version": 2,
        "surfaces": [
            {"name": "concept", "description": "Cross-cutting idea.",
             "required": ["type", "created", "description", "tags"], "optional": ["draft"]},
            {"name": "decision", "description": "Trade-off made.",
             "required": ["type", "created", "description", "tags"], "optional": ["superseded_by"]},
            {"name": "session", "description": "Curator session log.",
             "required": ["type", "created", "description"], "optional": ["scope", "tags"]},
            {"name": "paper", "description": "Publication.",
             "required": ["type", "created", "description", "tags", "citekey"], "optional": ["draft"],
             "plural": "papers", "slug_format": "{citekey}"},
        ],
    }
    val = handle_surface_validate(wiki="science", draft=draft)
    assert val["ok"] is True, val["issues"]

    draft_path = tmp_path / "draft.json"
    draft_path.write_text(json.dumps(draft))
    cli = runner.invoke(app, ["commit", str(draft_path)])
    assert cli.exit_code == 0, cli.output + cli.stderr

    from lore_core.surfaces import load_surfaces
    doc = load_surfaces(tmp_path / "wiki" / "science")
    assert [s.name for s in doc.surfaces] == ["concept", "decision", "session", "paper"]
