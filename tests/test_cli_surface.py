"""Tests for `lore surface add` / `lore surface lint` CLI commands."""
from __future__ import annotations

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


def test_surface_add_creates_bare_surfaces_md_when_missing(tmp_path, monkeypatch):
    """fresh wiki dir; surface add concept --wiki testwiki creates a minimal SURFACES.md with only concept.

    `add` no longer auto-seeds from the standard template — that's `init`'s job.
    A missing SURFACES.md gets a bare header + the requested surface appended.
    """
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    result = runner.invoke(app, ["add", "concept", "--wiki", "testwiki"])
    assert result.exit_code == 0, result.output + result.stderr
    surfaces_path = tmp_path / "wiki" / "testwiki" / "SURFACES.md"
    assert surfaces_path.exists()
    content = surfaces_path.read_text()
    assert "schema_version: 2" in content
    assert "## concept" in content
    # Template-only surfaces (decision/session) must NOT be pre-seeded
    assert "## decision" not in content
    assert "## session" not in content


def test_surface_add_creates_surfaces_md_when_missing_new_surface(tmp_path, monkeypatch):
    """fresh wiki dir; surface add newsurface --wiki testwiki creates minimal SURFACES.md and appends."""
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    result = runner.invoke(app, ["add", "newsurface", "--wiki", "testwiki"])
    assert result.exit_code == 0, result.output + result.stderr
    surfaces_path = tmp_path / "wiki" / "testwiki" / "SURFACES.md"
    assert surfaces_path.exists()
    content = surfaces_path.read_text()
    assert "schema_version: 2" in content
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


# ---------------------------------------------------------------------------
# surface init
# ---------------------------------------------------------------------------


def test_surface_init_seeds_from_standard_template(tmp_path, monkeypatch):
    """fresh wiki dir; surface init --wiki x seeds SURFACES.md from standard template."""
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    result = runner.invoke(app, ["init", "--wiki", "x"])
    assert result.exit_code == 0, result.output + result.stderr
    surfaces_path = tmp_path / "wiki" / "x" / "SURFACES.md"
    assert surfaces_path.exists()
    content = surfaces_path.read_text()
    assert "schema_version: 2" in content
    # Standard template ships concept/decision/session
    assert "## concept" in content
    assert "## decision" in content


def test_surface_init_uses_template_option(tmp_path, monkeypatch):
    """surface init --template science seeds from the science template."""
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    result = runner.invoke(app, ["init", "--wiki", "x", "--template", "science"])
    assert result.exit_code == 0, result.output + result.stderr
    content = (tmp_path / "wiki" / "x" / "SURFACES.md").read_text()
    assert "## paper" in content


def test_surface_init_refuses_to_overwrite(tmp_path, monkeypatch):
    """surface init refuses to overwrite an existing SURFACES.md without --force."""
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    wiki_dir = tmp_path / "wiki" / "x"
    _make_surfaces_md(wiki_dir, "# Surfaces\nschema_version: 2\n\n## existing\n")
    result = runner.invoke(app, ["init", "--wiki", "x"])
    assert result.exit_code == 1
    assert "already exists" in result.stderr.lower()
    # File unchanged
    assert "## existing" in (wiki_dir / "SURFACES.md").read_text()


def test_surface_init_force_overwrites(tmp_path, monkeypatch):
    """surface init --force overwrites an existing SURFACES.md."""
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    wiki_dir = tmp_path / "wiki" / "x"
    _make_surfaces_md(wiki_dir, "# Surfaces\nschema_version: 2\n\n## existing\n")
    result = runner.invoke(app, ["init", "--wiki", "x", "--force"])
    assert result.exit_code == 0, result.output + result.stderr
    content = (wiki_dir / "SURFACES.md").read_text()
    assert "## existing" not in content
    assert "## concept" in content


def test_surface_init_unknown_template_rejected(tmp_path, monkeypatch):
    """surface init --template nonsense exits non-zero."""
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    result = runner.invoke(app, ["init", "--wiki", "x", "--template", "nonsense"])
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
