"""Tests for `lore surface add` / `lore surface lint` CLI commands."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lore_cli.surface_cmd import app

runner = CliRunner(mix_stderr=False)


def _make_surfaces_md(wiki_dir: Path, content: str) -> Path:
    """Write a SURFACES.md file into wiki_dir."""
    wiki_dir.mkdir(parents=True, exist_ok=True)
    path = wiki_dir / "SURFACES.md"
    path.write_text(content)
    return path


# ---------------------------------------------------------------------------
# surface add
# ---------------------------------------------------------------------------


def test_surface_add_creates_surfaces_md_when_missing(tmp_path, monkeypatch):
    """fresh wiki dir; surface add concept --wiki testwiki creates SURFACES.md from standard template.

    The standard template already contains a ## concept section, so the command
    creates the file but then exits 1 with a duplicate-rejection (the file is
    created as a side-effect of initialising from the template).
    """
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    result = runner.invoke(app, ["add", "concept", "--wiki", "testwiki"])
    surfaces_path = tmp_path / "wiki" / "testwiki" / "SURFACES.md"
    # File must have been created (from standard template) even though we exit 1
    assert surfaces_path.exists()
    content = surfaces_path.read_text()
    # Standard template content is present
    assert "schema_version: 2" in content
    assert "## concept" in content
    # Duplicate rejected — the standard template already defines concept
    assert result.exit_code == 1
    assert "already exists" in result.stderr


def test_surface_add_creates_surfaces_md_when_missing_new_surface(tmp_path, monkeypatch):
    """fresh wiki dir; surface add newsurface --wiki testwiki creates SURFACES.md and appends."""
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    result = runner.invoke(app, ["add", "newsurface", "--wiki", "testwiki"])
    assert result.exit_code == 0, result.output + result.stderr
    surfaces_path = tmp_path / "wiki" / "testwiki" / "SURFACES.md"
    assert surfaces_path.exists()
    content = surfaces_path.read_text()
    # Standard template content is present
    assert "schema_version: 2" in content
    # New section appended
    assert "## newsurface" in content


def test_surface_add_appends_section_to_existing_file(tmp_path, monkeypatch):
    """pre-existing SURFACES.md with ## decision only; add my_surface appends it."""
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    wiki_dir = tmp_path / "wiki" / "x"
    _make_surfaces_md(
        wiki_dir,
        "# Surfaces\nschema_version: 2\n\n## decision\nA decision.\n\n```yaml\nrequired: [type]\noptional: []\n```\n",
    )
    result = runner.invoke(app, ["add", "my_surface", "--wiki", "x"])
    assert result.exit_code == 0, result.output + result.stderr
    content = (wiki_dir / "SURFACES.md").read_text()
    # Original section preserved
    assert "## decision" in content
    # New section appended
    assert "## my_surface" in content


def test_surface_add_rejects_duplicate_name(tmp_path, monkeypatch):
    """pre-existing file with ## concept; surface add concept exits 1 with 'already exists'."""
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    wiki_dir = tmp_path / "wiki" / "x"
    _make_surfaces_md(
        wiki_dir,
        "# Surfaces\nschema_version: 2\n\n## concept\nA concept.\n\n```yaml\nrequired: [type]\noptional: []\n```\n",
    )
    result = runner.invoke(app, ["add", "concept", "--wiki", "x"])
    assert result.exit_code == 1
    assert "already exists" in result.stderr


def test_surface_add_uses_template_initial_content(tmp_path, monkeypatch):
    """fresh wiki dir; surface add my_surface --template science creates SURFACES.md with science template."""
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    result = runner.invoke(app, ["add", "my_surface", "--wiki", "x", "--template", "science"])
    assert result.exit_code == 0, result.output + result.stderr
    surfaces_path = tmp_path / "wiki" / "x" / "SURFACES.md"
    assert surfaces_path.exists()
    content = surfaces_path.read_text()
    # Science template contains ## paper
    assert "## paper" in content
    # And my_surface was appended
    assert "## my_surface" in content


def test_surface_add_unknown_template_rejected(tmp_path, monkeypatch):
    """surface add x --wiki x --template nonsense exits non-zero."""
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    result = runner.invoke(app, ["add", "x", "--wiki", "x", "--template", "nonsense"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# surface lint
# ---------------------------------------------------------------------------


def test_surface_lint_accepts_well_formed_file(tmp_path, monkeypatch):
    """clean SURFACES.md; surface lint exits 0."""
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    wiki_dir = tmp_path / "wiki" / "x"
    _make_surfaces_md(
        wiki_dir,
        "# Surfaces\nschema_version: 2\n\n## concept\nA concept.\n\n```yaml\nrequired: [type, created]\noptional: [draft]\n```\n\n## decision\nA decision.\n\n```yaml\nrequired: [type, created]\noptional: []\n```\n",
    )
    result = runner.invoke(app, ["lint", "--wiki", "x"])
    assert result.exit_code == 0, result.output + result.stderr


def test_surface_lint_rejects_duplicate_section_name(tmp_path, monkeypatch):
    """SURFACES.md with ## concept declared twice; surface lint exits 1 mentioning duplicate."""
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    wiki_dir = tmp_path / "wiki" / "x"
    _make_surfaces_md(
        wiki_dir,
        "# Surfaces\nschema_version: 2\n\n## concept\nA concept.\n\n```yaml\nrequired: [type]\noptional: []\n```\n\n## concept\nDuplicate.\n\n```yaml\nrequired: [type]\noptional: []\n```\n",
    )
    result = runner.invoke(app, ["lint", "--wiki", "x"])
    assert result.exit_code == 1
    assert "duplicate" in result.stderr.lower()


def test_surface_lint_rejects_no_surfaces_md(tmp_path, monkeypatch):
    """fresh tmp dir without SURFACES.md → exit 0 with a 'no SURFACES.md' warning."""
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    # wiki/x dir exists but no SURFACES.md
    wiki_dir = tmp_path / "wiki" / "x"
    wiki_dir.mkdir(parents=True)
    result = runner.invoke(app, ["lint", "--wiki", "x"])
    assert result.exit_code == 0
    assert "no SURFACES.md" in result.stderr.lower() or "surfaces.md" in result.stderr.lower()


# ---------------------------------------------------------------------------
# surface commit
# ---------------------------------------------------------------------------


def test_surface_commit_append_on_missing_file(tmp_path, monkeypatch):
    """commit with operation=append creates a minimal file and appends the surface."""
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    draft = {
        "schema": "lore.surface.draft/1",
        "wiki": "x",
        "operation": "append",
        "surface": {
            "name": "paper",
            "description": "A paper.",
            "required": ["type", "created", "description", "tags"],
            "optional": ["draft"],
            "plural": "papers",
        },
    }
    draft_path = tmp_path / "draft.json"
    draft_path.write_text(json.dumps(draft))
    result = runner.invoke(app, ["commit", str(draft_path)])
    assert result.exit_code == 0, result.output + result.stderr
    content = (tmp_path / "wiki" / "x" / "SURFACES.md").read_text()
    assert "schema_version: 2" in content
    assert "## paper" in content
    assert "plural: papers" in content


def test_surface_commit_append_rejects_duplicate(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    wiki_dir = tmp_path / "wiki" / "x"
    _make_surfaces_md(
        wiki_dir,
        "# Surfaces\nschema_version: 2\n\n## paper\nX.\n\n```yaml\nrequired: [type]\noptional: []\n```\n",
    )
    draft = {
        "schema": "lore.surface.draft/1",
        "wiki": "x",
        "operation": "append",
        "surface": {"name": "paper", "description": "Y.", "required": ["type"], "optional": []},
    }
    draft_path = tmp_path / "draft.json"
    draft_path.write_text(json.dumps(draft))
    result = runner.invoke(app, ["commit", str(draft_path)])
    assert result.exit_code == 1
    assert "duplicate_name" in result.stderr or "already exists" in result.stderr.lower()


def test_surface_commit_force_overrides_duplicate(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    wiki_dir = tmp_path / "wiki" / "x"
    _make_surfaces_md(
        wiki_dir,
        "# Surfaces\nschema_version: 2\n\n## paper\nX.\n\n```yaml\nrequired: [type]\noptional: []\n```\n",
    )
    draft = {
        "schema": "lore.surface.draft/1",
        "wiki": "x",
        "operation": "append",
        "surface": {"name": "paper", "description": "Y (updated).", "required": ["type"], "optional": []},
    }
    draft_path = tmp_path / "draft.json"
    draft_path.write_text(json.dumps(draft))
    result = runner.invoke(app, ["commit", str(draft_path), "--force"])
    assert result.exit_code == 0, result.output + result.stderr
    content = (wiki_dir / "SURFACES.md").read_text()
    assert content.count("## paper") == 2


def test_surface_commit_rejects_invalid_schema(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    draft = {"schema": "wrong/1", "wiki": "x", "operation": "append", "surface": {}}
    draft_path = tmp_path / "draft.json"
    draft_path.write_text(json.dumps(draft))
    result = runner.invoke(app, ["commit", str(draft_path)])
    assert result.exit_code == 1
    assert "unknown_schema" in result.stderr or "unsupported" in result.stderr.lower()


def test_surface_commit_init_writes_full_file(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    draft = {
        "schema": "lore.surface.draft/1",
        "wiki": "science",
        "operation": "init",
        "schema_version": 2,
        "surfaces": [
            {"name": "concept", "description": "X.", "required": ["type"], "optional": []},
            {"name": "paper", "description": "Y.", "required": ["type", "citekey"], "optional": [], "plural": "papers", "slug_format": "{citekey}"},
        ],
    }
    draft_path = tmp_path / "draft.json"
    draft_path.write_text(json.dumps(draft))
    result = runner.invoke(app, ["commit", str(draft_path)])
    assert result.exit_code == 0, result.output + result.stderr
    content = (tmp_path / "wiki" / "science" / "SURFACES.md").read_text()
    assert content.startswith("# Surfaces — science\n")
    assert "## concept" in content
    assert "## paper" in content
    assert "plural: papers" in content


def test_surface_commit_init_refuses_if_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    _make_surfaces_md(tmp_path / "wiki" / "x", "# Surfaces\nschema_version: 2\n")
    draft = {
        "schema": "lore.surface.draft/1",
        "wiki": "x",
        "operation": "init",
        "schema_version": 2,
        "surfaces": [{"name": "concept", "description": "", "required": ["type"], "optional": []}],
    }
    draft_path = tmp_path / "draft.json"
    draft_path.write_text(json.dumps(draft))
    result = runner.invoke(app, ["commit", str(draft_path)])
    assert result.exit_code == 1
    assert "already exists" in result.stderr.lower()


def test_surface_commit_init_force_overwrites(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    _make_surfaces_md(tmp_path / "wiki" / "x", "# Surfaces\nschema_version: 2\n\n## old\nO.\n\n```yaml\nrequired: [type]\noptional: []\n```\n")
    draft = {
        "schema": "lore.surface.draft/1",
        "wiki": "x",
        "operation": "init",
        "schema_version": 2,
        "surfaces": [{"name": "fresh", "description": "F.", "required": ["type"], "optional": []}],
    }
    draft_path = tmp_path / "draft.json"
    draft_path.write_text(json.dumps(draft))
    result = runner.invoke(app, ["commit", str(draft_path), "--force"])
    assert result.exit_code == 0, result.output + result.stderr
    content = (tmp_path / "wiki" / "x" / "SURFACES.md").read_text()
    assert "## old" not in content
    assert "## fresh" in content


# ---------------------------------------------------------------------------
# surface commit — receipt shape contract tests
# ---------------------------------------------------------------------------


def test_surface_commit_receipt_shape_append(tmp_path, monkeypatch):
    """The receipt JSON on stdout matches the documented schema."""
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    draft = {
        "schema": "lore.surface.draft/1",
        "wiki": "x",
        "operation": "append",
        "surface": {"name": "paper", "description": "A.", "required": ["type"], "optional": []},
    }
    draft_path = tmp_path / "draft.json"
    draft_path.write_text(json.dumps(draft))
    result = runner.invoke(app, ["commit", str(draft_path)])
    assert result.exit_code == 0
    receipt = json.loads(result.stdout)
    assert receipt["schema"] == "lore.surface.commit/1"
    assert receipt["data"]["operation"] == "append"
    assert receipt["data"]["name"] == "paper"
    assert receipt["data"]["path"].endswith("SURFACES.md")


def test_surface_commit_receipt_shape_init(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    draft = {
        "schema": "lore.surface.draft/1",
        "wiki": "x",
        "operation": "init",
        "schema_version": 2,
        "surfaces": [
            {"name": "a", "description": "A.", "required": ["type"], "optional": []},
            {"name": "b", "description": "B.", "required": ["type"], "optional": []},
        ],
    }
    draft_path = tmp_path / "draft.json"
    draft_path.write_text(json.dumps(draft))
    result = runner.invoke(app, ["commit", str(draft_path)])
    receipt = json.loads(result.stdout)
    assert receipt["schema"] == "lore.surface.commit/1"
    assert receipt["data"]["operation"] == "init"
    assert receipt["data"]["surfaces"] == ["a", "b"]
