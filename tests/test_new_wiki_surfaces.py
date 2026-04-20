"""Tests for `lore new-wiki --surfaces` template selection."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from lore_cli.__main__ import app

runner = CliRunner(mix_stderr=False)


@pytest.fixture
def lore_root(tmp_path: Path, monkeypatch) -> Path:
    """Set LORE_ROOT to a temporary directory."""
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    return tmp_path


def test_new_wiki_writes_surfaces_md_with_default_template(lore_root: Path) -> None:
    """Test that `lore new-wiki testwiki` creates SURFACES.md with standard template."""
    result = runner.invoke(app, ["new-wiki", "testwiki"])
    assert result.exit_code == 0, f"Command failed: {result.stdout}\n{result.stderr}"

    surfaces_path = lore_root / "wiki" / "testwiki" / "SURFACES.md"
    assert surfaces_path.exists(), f"SURFACES.md not created at {surfaces_path}"

    content = surfaces_path.read_text()
    # Standard template should contain these sections
    assert "## concept" in content, "Standard template should contain '## concept'"
    assert "## decision" in content, "Standard template should contain '## decision'"


def test_new_wiki_uses_specified_template(lore_root: Path) -> None:
    """Test that `lore new-wiki --surfaces science sci` uses science template."""
    result = runner.invoke(app, ["new-wiki", "--surfaces", "science", "sci"])
    assert result.exit_code == 0, f"Command failed: {result.stdout}\n{result.stderr}"

    surfaces_path = lore_root / "wiki" / "sci" / "SURFACES.md"
    assert surfaces_path.exists()

    content = surfaces_path.read_text()
    # Science template should contain paper and result sections
    assert "## paper" in content, "Science template should contain '## paper'"
    assert "## result" in content, "Science template should contain '## result'"


def test_new_wiki_rejects_unknown_template(lore_root: Path) -> None:
    """Test that `lore new-wiki --surfaces nonsense x` exits with error."""
    result = runner.invoke(app, ["new-wiki", "--surfaces", "nonsense", "x"])
    assert result.exit_code != 0, "Should fail with unknown template"
    assert "unknown template" in result.stderr.lower() or "unknown template" in result.stdout.lower(), \
        f"Error message should mention unknown template. stderr: {result.stderr}, stdout: {result.stdout}"
    # Should mention valid templates
    assert ("standard" in result.stderr or "standard" in result.stdout), \
        "Error should mention available templates"


def test_new_wiki_existing_wiki_does_not_clobber_surfaces_md(lore_root: Path) -> None:
    """Test that existing SURFACES.md is not overwritten."""
    wiki_dir = lore_root / "wiki" / "x"
    wiki_dir.mkdir(parents=True, exist_ok=True)

    # Pre-create SURFACES.md with custom content
    surfaces_path = wiki_dir / "SURFACES.md"
    custom_content = "# Custom SURFACES\n\nThis should not be overwritten.\n"
    surfaces_path.write_text(custom_content)

    # Run new-wiki on existing wiki
    result = runner.invoke(app, ["new-wiki", "--surfaces", "standard", "x"])
    # Should succeed (or at least not crash)
    # The wiki directory already exists, so it may hit force check or may succeed

    # Check that content was NOT clobbered
    assert surfaces_path.read_text() == custom_content, \
        "Existing SURFACES.md should not be overwritten"


def test_new_wiki_existing_wiki_writes_surfaces_when_missing(lore_root: Path) -> None:
    """Test that SURFACES.md is written to existing wiki without SURFACES.md."""
    wiki_dir = lore_root / "wiki" / "x"
    wiki_dir.mkdir(parents=True, exist_ok=True)

    # Pre-create wiki directory but without SURFACES.md
    # Run new-wiki on existing wiki
    result = runner.invoke(app, ["new-wiki", "--surfaces", "standard", "x"])
    # Should succeed

    surfaces_path = wiki_dir / "SURFACES.md"
    assert surfaces_path.exists(), "SURFACES.md should be created in existing wiki"

    content = surfaces_path.read_text()
    assert "## concept" in content, "Should use standard template"
    assert "## decision" in content, "Should use standard template"
