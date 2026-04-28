"""Tests for lore_core.session_template — Phase 5 per-wiki templates."""
from __future__ import annotations

from pathlib import Path

from lore_core.session_template import (
    _load_packaged_standard,
    load_session_template,
    section_norms_for_prompt,
)


def test_load_returns_packaged_standard_when_no_override(tmp_path):
    """No per-wiki override → the shipped standard.md is returned."""
    wiki_dir = tmp_path / "wiki" / "private"
    wiki_dir.mkdir(parents=True)
    text = load_session_template(wiki_dir)
    assert "Session-note template" in text
    assert "## Section-authoring norms" in text


def test_load_returns_per_wiki_override_when_present(tmp_path):
    """``<wiki>/templates/session.md`` replaces the shipped default."""
    wiki_dir = tmp_path / "wiki" / "private"
    (wiki_dir / "templates").mkdir(parents=True)
    override = wiki_dir / "templates" / "session.md"
    override.write_text("# wiki override\n\nSpecial rules for this wiki.\n")

    text = load_session_template(wiki_dir)
    assert "wiki override" in text
    assert "Session-note template" not in text


def test_load_returns_packaged_standard_for_none_wiki_dir():
    """Callers without an attached wiki (CLI tools, tests) still get
    the shipped default."""
    text = load_session_template(None)
    assert "## Section-authoring norms" in text


def test_section_norms_for_prompt_slices_norms_block(tmp_path):
    """The prompt-injection slice starts at the
    ``## Section-authoring norms`` heading."""
    wiki_dir = tmp_path / "wiki" / "private"
    wiki_dir.mkdir(parents=True)
    norms = section_norms_for_prompt(wiki_dir)
    assert norms.startswith("## Section-authoring norms")
    # Norms must mention the four LLM-authored sections.
    assert "## Summary" in norms
    assert "## Decisions made" in norms
    assert "## What we worked on" in norms
    assert "## Loose ends" in norms


def test_section_norms_falls_back_to_full_template_when_marker_missing(tmp_path):
    """If a per-wiki override drops the norms section heading entirely,
    the prompt injection falls back to the whole template — better to
    over-include than to silently lose steering."""
    wiki_dir = tmp_path / "wiki" / "private"
    (wiki_dir / "templates").mkdir(parents=True)
    (wiki_dir / "templates" / "session.md").write_text(
        "# overridden\n\nNo norms section here.\n"
    )
    norms = section_norms_for_prompt(wiki_dir)
    assert "overridden" in norms


# ---------------------------------------------------------------------------
# Drift test: the renderer's emitted headings must all be mentioned in
# standard.md so a future contributor editing the renderer can't silently
# diverge from the documented contract.
# ---------------------------------------------------------------------------


def test_standard_template_mentions_every_renderer_heading():
    """Renderer emits a fixed set of H1 / H2 / H3 headings (see
    ``lore_core.session_writer.render_body_sections``). The shipped
    template must mention every one of them so the documentation
    stays in lock-step with the code.
    """
    template = _load_packaged_standard()
    expected_headings = [
        # H2 sections
        "## Summary",
        "## Decisions made",
        "## What we worked on",
        "## Activity",
        "## Loose ends",
        # H3 subsections under Activity
        "### Commits",
        "### Issues opened",
        "### Issues closed",
    ]
    missing = [h for h in expected_headings if h not in template]
    assert not missing, (
        f"Template at session_templates/standard.md is missing the following "
        f"headings the renderer emits: {missing}. Either add them to the "
        f"template or update render_body_sections."
    )
