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
# surface add — launcher tests
# ---------------------------------------------------------------------------


def test_surface_add_launcher_execs_claude_with_skill_and_wiki(tmp_path, monkeypatch):
    """`lore surface add --wiki X` exec's `claude "/lore:surface-new X"`."""
    import os
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    (tmp_path / "wiki" / "science").mkdir(parents=True)
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    record_file = tmp_path / "claude-invocation.txt"
    shim = shim_dir / "claude"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$@\" > {record_file}\n"
    )
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim_dir}{os.pathsep}{os.environ['PATH']}")
    result = runner.invoke(app, ["add", "--wiki", "science"])
    assert result.exit_code == 0, result.output + result.stderr
    assert record_file.read_text().strip() == "/lore:surface-new science"


def test_surface_add_launcher_missing_claude_prints_helpful_error(tmp_path, monkeypatch):
    """If `claude` is not on PATH, exit 1 with an install pointer."""
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    (tmp_path / "wiki" / "science").mkdir(parents=True)
    monkeypatch.setenv("PATH", str(tmp_path))  # no claude here
    result = runner.invoke(app, ["add", "--wiki", "science"])
    assert result.exit_code == 1
    assert "claude" in result.stderr.lower()
    assert "install" in result.stderr.lower() or "path" in result.stderr.lower()


# ---------------------------------------------------------------------------
# surface init — launcher tests
# ---------------------------------------------------------------------------


def test_surface_init_launcher_execs_claude(tmp_path, monkeypatch):
    import os
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    (tmp_path / "wiki" / "science").mkdir(parents=True)
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    record = tmp_path / "claude-args.txt"
    shim = shim_dir / "claude"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$@\" > {record}\n"
    )
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim_dir}{os.pathsep}{os.environ['PATH']}")
    result = runner.invoke(app, ["init", "--wiki", "science"])
    assert result.exit_code == 0, result.output + result.stderr
    assert record.read_text().strip() == "/lore:surface-init science"


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


def test_surface_lint_catches_plural_collision(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    wiki_dir = tmp_path / "wiki" / "x"
    _make_surfaces_md(
        wiki_dir,
        "# Surfaces\nschema_version: 2\n\n"
        "## paper\nA.\n\n```yaml\nrequired: [type]\noptional: []\nplural: papers\n```\n\n"
        "## study\nB.\n\n```yaml\nrequired: [type]\noptional: []\nplural: papers\n```\n",
    )
    result = runner.invoke(app, ["lint", "--wiki", "x"])
    assert result.exit_code == 1
    assert "plural" in result.stderr.lower()


def test_surface_lint_catches_invalid_slug_format(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    wiki_dir = tmp_path / "wiki" / "x"
    _make_surfaces_md(
        wiki_dir,
        "# Surfaces\nschema_version: 2\n\n"
        "## paper\nA.\n\n```yaml\nrequired: [type]\noptional: []\nslug_format: \"{nonsense}\"\n```\n",
    )
    result = runner.invoke(app, ["lint", "--wiki", "x"])
    assert result.exit_code == 1
    assert "slug_format" in result.stderr.lower() or "placeholder" in result.stderr.lower()


def test_surface_lint_catches_invalid_plural_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    wiki_dir = tmp_path / "wiki" / "x"
    _make_surfaces_md(
        wiki_dir,
        "# Surfaces\nschema_version: 2\n\n"
        "## paper\nA.\n\n```yaml\nrequired: [type]\noptional: []\nplural: \"Bad Plural!\"\n```\n",
    )
    result = runner.invoke(app, ["lint", "--wiki", "x"])
    assert result.exit_code == 1
    assert "plural" in result.stderr.lower()


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
