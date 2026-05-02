"""Tests for Curator C cross-scope concept hoist pass (Phase 4).

The pass scans project folders for concepts whose slugs recur across
≥2 sibling projects and proposes hoisting them up to the parent
project's ``concepts/`` folder. Auto-stubs the parent project when
missing. Proposal-only — never edits source notes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lore_curator.c_cross_scope_hoist import cross_scope_hoist_pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_orientation(wiki: Path, project_slug: str, scope: str) -> None:
    proj = wiki / "projects" / project_slug
    proj.mkdir(parents=True, exist_ok=True)
    (proj / f"{project_slug}.md").write_text(
        "---\n"
        "schema_version: 2\n"
        "type: project\n"
        f"created: '2026-05-01'\n"
        f"last_reviewed: '2026-05-01'\n"
        f"description: '{project_slug}'\n"
        f"tags: [project]\n"
        f"scope: {scope}\n"
        "---\n\n"
        f"# Project: {project_slug}\n"
    )


def _write_concept(wiki: Path, project_slug: str, concept_slug: str) -> Path:
    proj = wiki / "projects" / project_slug
    concepts = proj / "concepts"
    concepts.mkdir(parents=True, exist_ok=True)
    p = concepts / f"{concept_slug}.md"
    p.write_text(
        "---\n"
        "type: concept\n"
        "created: '2026-05-01'\n"
        "last_reviewed: '2026-05-01'\n"
        f"description: '{concept_slug}'\n"
        "tags: [topic/x]\n"
        f"scope: {_scope_for_project(wiki, project_slug)}\n"
        "---\n\n"
        f"# {concept_slug}\n"
    )
    return p


def _scope_for_project(wiki: Path, project_slug: str) -> str:
    orient = wiki / "projects" / project_slug / f"{project_slug}.md"
    for line in orient.read_text().splitlines():
        if line.startswith("scope:"):
            return line.split(":", 1)[1].strip()
    return ""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_hoist_pass_skipped_when_toggle_off(tmp_path, monkeypatch):
    monkeypatch.delenv("LORE_PROJECT_FOLDERS", raising=False)
    _write_orientation(tmp_path, "ops-db", "ccat:data-center:ops-db")
    _write_orientation(tmp_path, "data-transfer", "ccat:data-center:data-transfer")
    _write_concept(tmp_path, "ops-db", "event-sourcing-pattern")
    _write_concept(tmp_path, "data-transfer", "event-sourcing-pattern")

    counts = cross_scope_hoist_pass(tmp_path, llm_client=None, dry_run=False)
    assert counts.get("cross_scope_hoist_skipped_toggle_off") == 1


def test_hoist_pass_proposes_when_two_siblings_share_concept(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_PROJECT_FOLDERS", "on")
    _write_orientation(tmp_path, "ops-db", "ccat:data-center:ops-db")
    _write_orientation(tmp_path, "data-transfer", "ccat:data-center:data-transfer")
    _write_concept(tmp_path, "ops-db", "event-sourcing-pattern")
    _write_concept(tmp_path, "data-transfer", "event-sourcing-pattern")

    counts = cross_scope_hoist_pass(tmp_path, llm_client=None, dry_run=False)
    assert counts["cross_scope_hoist_proposed"] == 1

    # The pass must auto-stub the parent project folder.
    parent_orient = tmp_path / "projects" / "data-center" / "data-center.md"
    assert parent_orient.exists(), (
        "hoist pass must auto-stub the parent project orientation"
    )

    proposal = (
        tmp_path / "projects" / "data-center" / "concepts"
        / "proposed-hoist-event-sourcing-pattern.md"
    )
    assert proposal.exists()
    text = proposal.read_text()
    assert "hoist_candidate_sources:" in text
    assert "[[event-sourcing-pattern]]" in text
    assert "ccat:data-center" in text


def test_hoist_pass_skips_when_only_one_sibling_has_concept(tmp_path, monkeypatch):
    """Pair-level recurrence is adjacent-merge territory, not hoist."""
    monkeypatch.setenv("LORE_PROJECT_FOLDERS", "on")
    _write_orientation(tmp_path, "ops-db", "ccat:data-center:ops-db")
    _write_orientation(tmp_path, "data-transfer", "ccat:data-center:data-transfer")
    _write_concept(tmp_path, "ops-db", "ops-db-only")
    _write_concept(tmp_path, "data-transfer", "transfer-only")
    # Slugs are too different to fuzz-match.

    counts = cross_scope_hoist_pass(tmp_path, llm_client=None, dry_run=False)
    assert counts["cross_scope_hoist_proposed"] == 0


def test_hoist_pass_uses_existing_parent_project_folder(tmp_path, monkeypatch):
    """Parent project already exists → pass does not auto-stub a new one,
    just files the proposal inside the existing parent."""
    monkeypatch.setenv("LORE_PROJECT_FOLDERS", "on")
    _write_orientation(tmp_path, "ops-db", "ccat:data-center:ops-db")
    _write_orientation(tmp_path, "data-transfer", "ccat:data-center:data-transfer")
    _write_orientation(tmp_path, "data-center", "ccat:data-center")
    _write_concept(tmp_path, "ops-db", "event-sourcing-pattern")
    _write_concept(tmp_path, "data-transfer", "event-sourcing-pattern")

    pre_text = (tmp_path / "projects" / "data-center" / "data-center.md").read_text()

    counts = cross_scope_hoist_pass(tmp_path, llm_client=None, dry_run=False)
    assert counts["cross_scope_hoist_proposed"] == 1

    # Existing parent orientation MUST not be clobbered.
    post_text = (tmp_path / "projects" / "data-center" / "data-center.md").read_text()
    assert pre_text == post_text, (
        "hoist pass must not modify existing parent orientation"
    )


def test_hoist_pass_idempotent(tmp_path, monkeypatch):
    """Second run finds the proposal already exists and skips."""
    monkeypatch.setenv("LORE_PROJECT_FOLDERS", "on")
    _write_orientation(tmp_path, "ops-db", "ccat:data-center:ops-db")
    _write_orientation(tmp_path, "data-transfer", "ccat:data-center:data-transfer")
    _write_concept(tmp_path, "ops-db", "event-sourcing-pattern")
    _write_concept(tmp_path, "data-transfer", "event-sourcing-pattern")

    cross_scope_hoist_pass(tmp_path, llm_client=None, dry_run=False)
    counts = cross_scope_hoist_pass(tmp_path, llm_client=None, dry_run=False)
    assert counts["cross_scope_hoist_existing"] == 1
    assert counts["cross_scope_hoist_proposed"] == 0


def test_hoist_pass_does_not_edit_source_concepts(tmp_path, monkeypatch):
    """Originals stay untouched — Curator C is proposal-only."""
    monkeypatch.setenv("LORE_PROJECT_FOLDERS", "on")
    _write_orientation(tmp_path, "ops-db", "ccat:data-center:ops-db")
    _write_orientation(tmp_path, "data-transfer", "ccat:data-center:data-transfer")
    a = _write_concept(tmp_path, "ops-db", "event-sourcing-pattern")
    b = _write_concept(tmp_path, "data-transfer", "event-sourcing-pattern")
    pre_a = a.read_text()
    pre_b = b.read_text()

    cross_scope_hoist_pass(tmp_path, llm_client=None, dry_run=False)

    assert a.read_text() == pre_a
    assert b.read_text() == pre_b


def test_hoist_pass_dry_run_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_PROJECT_FOLDERS", "on")
    _write_orientation(tmp_path, "ops-db", "ccat:data-center:ops-db")
    _write_orientation(tmp_path, "data-transfer", "ccat:data-center:data-transfer")
    _write_concept(tmp_path, "ops-db", "event-sourcing-pattern")
    _write_concept(tmp_path, "data-transfer", "event-sourcing-pattern")

    counts = cross_scope_hoist_pass(tmp_path, llm_client=None, dry_run=True)
    assert counts["cross_scope_hoist_proposed"] == 1

    parent_orient = tmp_path / "projects" / "data-center" / "data-center.md"
    proposal = (
        tmp_path / "projects" / "data-center" / "concepts"
        / "proposed-hoist-event-sourcing-pattern.md"
    )
    assert not parent_orient.exists()
    assert not proposal.exists()


def test_hoist_pass_refuses_on_slug_collision(tmp_path, monkeypatch):
    """If the parent slug collides with a non-project basename anywhere
    in the wiki, auto-stub is refused for that hoist candidate."""
    monkeypatch.setenv("LORE_PROJECT_FOLDERS", "on")
    _write_orientation(tmp_path, "ops-db", "ccat:data-center:ops-db")
    _write_orientation(tmp_path, "data-transfer", "ccat:data-center:data-transfer")
    _write_concept(tmp_path, "ops-db", "event-sourcing-pattern")
    _write_concept(tmp_path, "data-transfer", "event-sourcing-pattern")

    # A non-project note already owns the slug ``data-center``.
    (tmp_path / "concepts").mkdir(exist_ok=True)
    (tmp_path / "concepts" / "data-center.md").write_text(
        "---\ntype: concept\n---\n# Data Center\n"
    )

    counts = cross_scope_hoist_pass(tmp_path, llm_client=None, dry_run=False)
    assert counts["cross_scope_hoist_skipped_collision"] == 1
    # No parent folder created, no proposal written.
    parent_orient = tmp_path / "projects" / "data-center" / "data-center.md"
    proposal = (
        tmp_path / "projects" / "data-center" / "concepts"
        / "proposed-hoist-event-sourcing-pattern.md"
    )
    assert not parent_orient.exists()
    assert not proposal.exists()


def test_hoist_pass_only_groups_actual_siblings(tmp_path, monkeypatch):
    """Projects whose scopes do NOT share a parent are not hoist candidates."""
    monkeypatch.setenv("LORE_PROJECT_FOLDERS", "on")
    _write_orientation(tmp_path, "ops-db", "ccat:data-center:ops-db")
    _write_orientation(tmp_path, "telescope", "ccat:telescope")
    _write_concept(tmp_path, "ops-db", "event-sourcing-pattern")
    _write_concept(tmp_path, "telescope", "event-sourcing-pattern")

    # ``ccat:data-center:ops-db`` and ``ccat:telescope`` do NOT share an
    # immediate parent (data-center vs telescope). They share grandparent
    # ``ccat`` only — strict-immediate-parent rule rejects.
    counts = cross_scope_hoist_pass(tmp_path, llm_client=None, dry_run=False)
    assert counts["cross_scope_hoist_proposed"] == 0


def test_hoist_pass_handles_three_siblings(tmp_path, monkeypatch):
    """Three sibling projects all sharing a concept → one hoist proposal
    listing all three sources."""
    monkeypatch.setenv("LORE_PROJECT_FOLDERS", "on")
    _write_orientation(tmp_path, "ops-db", "ccat:data-center:ops-db")
    _write_orientation(tmp_path, "data-transfer", "ccat:data-center:data-transfer")
    _write_orientation(tmp_path, "workflow-manager", "ccat:data-center:workflow-manager")
    _write_concept(tmp_path, "ops-db", "atomic-write")
    _write_concept(tmp_path, "data-transfer", "atomic-write")
    _write_concept(tmp_path, "workflow-manager", "atomic-write")

    counts = cross_scope_hoist_pass(tmp_path, llm_client=None, dry_run=False)
    assert counts["cross_scope_hoist_proposed"] == 1

    proposal = (
        tmp_path / "projects" / "data-center" / "concepts"
        / "proposed-hoist-atomic-write.md"
    )
    assert proposal.exists()
    text = proposal.read_text()
    # All three sources should appear.
    assert "ops-db" in text
    assert "data-transfer" in text
    assert "workflow-manager" in text
