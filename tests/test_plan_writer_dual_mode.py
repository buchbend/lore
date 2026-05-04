"""Integration tests for ``write_plan_note`` Phase 5 dual-mode."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from lore_core.plans.types import PlanStep, StructuredPlan
from lore_core.plans.registry import list_active, read_one
from lore_core.plans.writer import compute_source_hash, write_plan_note


def _make_plan(slug: str = "my-feature") -> StructuredPlan:
    return StructuredPlan(
        slug=slug,
        title=f"Plan {slug}",
        body_intro="Intro line.",
        steps=[
            PlanStep(id="step-1", title="Do thing", body="step body"),
        ],
        mode="single",
        confidence="high",
    )


def test_write_plan_legacy_path_when_toggle_off(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_PROJECT_FOLDERS", "off")
    plan = _make_plan()
    result = write_plan_note(
        wiki_root=tmp_path,
        plan=plan,
        source_hash=compute_source_hash("text"),
        source_adapter="claude-code",
        repo="ccatobs/ops-db",
        scope="ccat:ops-db",
        today=date(2026, 5, 1),
    )
    assert result.path == tmp_path / "plans" / "my-feature.md"


def test_write_plan_project_folder_path_when_toggle_on(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_PROJECT_FOLDERS", "on")
    (tmp_path / "projects" / "ops-db").mkdir(parents=True)

    plan = _make_plan()
    result = write_plan_note(
        wiki_root=tmp_path,
        plan=plan,
        source_hash=compute_source_hash("text"),
        source_adapter="claude-code",
        repo="ccatobs/ops-db",
        scope="ccat:ops-db",
        today=date(2026, 5, 1),
    )
    expected = tmp_path / "projects" / "ops-db" / "plans" / "2026-05-01-my-feature.md"
    assert result.path == expected
    assert expected.exists()


def test_write_plan_dual_mode_idempotent_across_days(tmp_path, monkeypatch):
    """Re-capturing the same plan on a later day finds the original
    path; no duplicate gets written under a new date prefix."""
    monkeypatch.setenv("LORE_PROJECT_FOLDERS", "on")
    (tmp_path / "projects" / "ops-db").mkdir(parents=True)

    plan = _make_plan()
    src = compute_source_hash("text-v1")

    first = write_plan_note(
        wiki_root=tmp_path,
        plan=plan,
        source_hash=src,
        source_adapter="claude-code",
        scope="ccat:ops-db",
        today=date(2026, 5, 1),
    )

    # Re-capture with the same hash on a later day → deduped, same path.
    second = write_plan_note(
        wiki_root=tmp_path,
        plan=plan,
        source_hash=src,
        source_adapter="claude-code",
        scope="ccat:ops-db",
        today=date(2026, 5, 5),
    )

    assert first.path == second.path
    assert second.outcome == "deduped"

    # Only one plan file on disk.
    project_plans = tmp_path / "projects" / "ops-db" / "plans"
    plan_files = list(project_plans.glob("*.md"))
    assert len(plan_files) == 1
    assert plan_files[0].name == "2026-05-01-my-feature.md"


def test_list_active_finds_plans_in_both_layouts(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_PROJECT_FOLDERS", "on")
    # Legacy flat plan.
    flat = tmp_path / "plans"
    flat.mkdir()
    (flat / "legacy-plan.md").write_text(
        "---\n"
        "type: plan\n"
        "status: active\n"
        "slug: legacy-plan\n"
        "last_reviewed: '2026-05-01'\n"
        "description: legacy\n"
        "---\n"
    )
    # Project-folder plan.
    project_plans = tmp_path / "projects" / "ops-db" / "plans"
    project_plans.mkdir(parents=True)
    (project_plans / "2026-05-01-new-plan.md").write_text(
        "---\n"
        "type: plan\n"
        "status: active\n"
        "slug: new-plan\n"
        "last_reviewed: '2026-05-01'\n"
        "description: new\n"
        "---\n"
    )

    cards = list_active(tmp_path)
    slugs = [c.slug for c in cards]
    assert "legacy-plan" in slugs
    assert "new-plan" in slugs


def test_read_one_resolves_project_folder_plan(tmp_path, monkeypatch):
    """``read_one(slug)`` finds a date-prefixed project-folder plan
    by its slug regardless of date prefix."""
    monkeypatch.setenv("LORE_PROJECT_FOLDERS", "on")
    project_plans = tmp_path / "projects" / "ops-db" / "plans"
    project_plans.mkdir(parents=True)
    (project_plans / "2026-05-01-my-feature.md").write_text(
        "---\n"
        "type: plan\n"
        "status: active\n"
        "slug: my-feature\n"
        "last_reviewed: '2026-05-01'\n"
        "description: my feature\n"
        "---\n"
    )

    card = read_one(tmp_path, "my-feature")
    assert card is not None
    assert card.slug == "my-feature"
