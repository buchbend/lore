"""Tests for `lore_workflow.scaffold` — repo workflow scaffolding.

Ported from ccat-agent-workflow's `tests/test_workflow_init.py`. Only the
docs/prd, docs/adr, and AGENTS.md/CLAUDE.md shim mechanics are ported; the
settings.json permissions/hooks scaffolding is a sibling slice (#170) and is
out of scope here.
"""

from __future__ import annotations

from pathlib import Path

from lore_workflow import scaffold as mod


def test_empty_repo_creates_agents_md(tmp_path: Path) -> None:
    mod.scaffold(tmp_path)
    assert (tmp_path / "AGENTS.md").exists()


def test_empty_repo_creates_claude_md_shim(tmp_path: Path) -> None:
    mod.scaffold(tmp_path)
    claude_md = tmp_path / "CLAUDE.md"
    assert claude_md.exists()
    assert claude_md.read_text(encoding="utf-8").strip() == "@AGENTS.md"


def test_existing_claude_md_migrated_to_agents_md(tmp_path: Path) -> None:
    original = "# My Guide\n\nSome content.\n"
    (tmp_path / "CLAUDE.md").write_text(original, encoding="utf-8")
    mod.scaffold(tmp_path)
    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert original.strip() == agents.strip()
    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8").strip() == "@AGENTS.md"


def test_empty_repo_creates_docs_prd_index(tmp_path: Path) -> None:
    mod.scaffold(tmp_path)
    assert (tmp_path / "docs" / "prd" / "index.md").exists()


def test_empty_repo_creates_docs_adr_index(tmp_path: Path) -> None:
    mod.scaffold(tmp_path)
    assert (tmp_path / "docs" / "adr" / "index.md").exists()


def test_docs_adr_index_has_toctree(tmp_path: Path) -> None:
    mod.scaffold(tmp_path)
    text = (tmp_path / "docs" / "adr" / "index.md").read_text(encoding="utf-8")
    assert "```{toctree}" in text
    assert "{{" not in text


def test_fresh_repo_creates_docs_index_wired(tmp_path: Path) -> None:
    mod.scaffold(tmp_path)
    text = (tmp_path / "docs" / "index.md").read_text(encoding="utf-8")
    assert "prd/index" in text
    assert "adr/index" in text


def test_docs_index_gains_missing_entry_when_present(tmp_path: Path) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    docs_index = docs_dir / "index.md"
    docs_index.write_text("# Docs\n\n```{toctree}\nadr/index\n```\n", encoding="utf-8")
    mod.scaffold(tmp_path)
    text = docs_index.read_text(encoding="utf-8")
    assert "prd/index" in text
    assert text.count("adr/index") == 1


def test_existing_adr_index_not_overwritten(tmp_path: Path) -> None:
    adr_dir = tmp_path / "docs" / "adr"
    adr_dir.mkdir(parents=True)
    existing = "# ADR Index\n\n```{toctree}\n0001-existing\n```\n"
    (adr_dir / "index.md").write_text(existing, encoding="utf-8")
    mod.scaffold(tmp_path)
    assert (adr_dir / "index.md").read_text(encoding="utf-8") == existing


def test_coexistence_agents_md_not_duplicated_when_shim_present(tmp_path: Path) -> None:
    """If CLAUDE.md is already the shim, re-running must not re-append AGENTS.md."""
    (tmp_path / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("# Guide\n", encoding="utf-8")
    mod.scaffold(tmp_path)
    mod.scaffold(tmp_path)
    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert agents.count("# Guide") == 1


def test_idempotent_full_rerun_is_noop(tmp_path: Path) -> None:
    """Running scaffold twice on a fresh repo must not change any output byte."""
    mod.scaffold(tmp_path)
    snapshot = {p: p.read_text(encoding="utf-8") for p in tmp_path.rglob("*") if p.is_file()}
    mod.scaffold(tmp_path)
    for p, before in snapshot.items():
        assert p.read_text(encoding="utf-8") == before, f"{p} changed on re-run"


def test_scaffold_returns_whether_anything_changed(tmp_path: Path) -> None:
    assert mod.scaffold(tmp_path) is True
    assert mod.scaffold(tmp_path) is False
