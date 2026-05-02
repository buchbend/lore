"""Tests for plan path routing (Phase 5 dual-mode)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from lore_core.plans.router import (
    derive_project_slug,
    iter_plan_paths,
    plan_target_path,
)


# ---------------------------------------------------------------------------
# derive_project_slug
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "repo,scope,expected",
    [
        (None, "ccat:data-center:ops-db", "ops-db"),
        (None, "ccat", "ccat"),
        ("ccatobs/ops-db", None, "ops-db"),
        ("ops-db", None, "ops-db"),
        # Scope wins over repo when both present.
        ("ccatobs/foo", "ccat:bar", "bar"),
        (None, None, None),
        ("", "", None),
    ],
)
def test_derive_project_slug(repo, scope, expected):
    assert derive_project_slug(repo, scope) == expected


# ---------------------------------------------------------------------------
# plan_target_path
# ---------------------------------------------------------------------------


_TODAY = date(2026, 5, 1)


def test_legacy_path_when_toggle_off(tmp_path, monkeypatch):
    monkeypatch.delenv("LORE_PROJECT_FOLDERS", raising=False)
    p = plan_target_path(tmp_path, "my-feature", _TODAY,
                          repo="ccatobs/ops-db", scope="ccat:ops-db")
    assert p == tmp_path / "plans" / "my-feature.md"


def test_project_folder_path_when_toggle_on_and_match(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_PROJECT_FOLDERS", "on")
    (tmp_path / "projects" / "ops-db").mkdir(parents=True)
    p = plan_target_path(tmp_path, "my-feature", _TODAY,
                          repo=None, scope="ccat:ops-db")
    assert p == tmp_path / "projects" / "ops-db" / "plans" / "2026-05-01-my-feature.md"


def test_falls_back_to_date_prefixed_flat_when_no_project_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_PROJECT_FOLDERS", "on")
    # No projects/ subtree at all.
    p = plan_target_path(tmp_path, "my-feature", _TODAY,
                          scope="ccat:nope")
    assert p == tmp_path / "plans" / "2026-05-01-my-feature.md"


def test_resolves_via_repo_when_scope_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_PROJECT_FOLDERS", "on")
    (tmp_path / "projects" / "ops-db").mkdir(parents=True)
    p = plan_target_path(tmp_path, "my-feature", _TODAY,
                          repo="ccatobs/ops-db", scope=None)
    assert p == tmp_path / "projects" / "ops-db" / "plans" / "2026-05-01-my-feature.md"


# ---------------------------------------------------------------------------
# iter_plan_paths — dual-mode read scan
# ---------------------------------------------------------------------------


def test_iter_plan_paths_legacy_only(tmp_path):
    plans = tmp_path / "plans"
    plans.mkdir()
    (plans / "alpha.md").write_text("---\ntype: plan\n---\n")
    (plans / "bravo.md").write_text("---\ntype: plan\n---\n")
    paths = list(iter_plan_paths(tmp_path))
    names = [p.name for p in paths]
    assert "alpha.md" in names
    assert "bravo.md" in names


def test_iter_plan_paths_project_folder_only(tmp_path):
    proj_plans = tmp_path / "projects" / "ops-db" / "plans"
    proj_plans.mkdir(parents=True)
    (proj_plans / "2026-05-01-foo.md").write_text("---\ntype: plan\n---\n")
    paths = list(iter_plan_paths(tmp_path))
    assert paths[0].name == "2026-05-01-foo.md"


def test_iter_plan_paths_mixed(tmp_path):
    """Vault with some legacy and some project-folder plans."""
    flat = tmp_path / "plans"
    flat.mkdir()
    (flat / "legacy.md").write_text("---\ntype: plan\n---\n")

    proj_plans = tmp_path / "projects" / "ops-db" / "plans"
    proj_plans.mkdir(parents=True)
    (proj_plans / "2026-05-01-new.md").write_text("---\ntype: plan\n---\n")

    paths = list(iter_plan_paths(tmp_path))
    names = [p.name for p in paths]
    assert "legacy.md" in names
    assert "2026-05-01-new.md" in names


def test_iter_plan_paths_empty_wiki(tmp_path):
    """No plans/ and no projects/ — yields nothing without erroring."""
    paths = list(iter_plan_paths(tmp_path))
    assert paths == []


def test_find_existing_plan_path_skips_project_folders_when_toggle_off(
    tmp_path, monkeypatch,
):
    """Regression: with ``LORE_PROJECT_FOLDERS=off`` the existing-plan
    finder must NOT scan project folders. Stray content under
    ``projects/<x>/plans/`` (legacy migrations, manual moves) gets
    ignored so the off-path is byte-for-byte identical to pre-rollout
    behaviour.
    """
    monkeypatch.delenv("LORE_PROJECT_FOLDERS", raising=False)
    proj_plans = tmp_path / "projects" / "ops-db" / "plans"
    proj_plans.mkdir(parents=True)
    (proj_plans / "2026-05-01-my-feature.md").write_text("---\ntype: plan\n---\n")

    # Legacy flat is empty.
    (tmp_path / "plans").mkdir()

    from lore_core.plans.router import find_existing_plan_path
    assert find_existing_plan_path(tmp_path, "my-feature") is None


def test_find_existing_plan_path_finds_project_folders_when_toggle_on(
    tmp_path, monkeypatch,
):
    """With the toggle on, the same setup IS found."""
    monkeypatch.setenv("LORE_PROJECT_FOLDERS", "on")
    proj_plans = tmp_path / "projects" / "ops-db" / "plans"
    proj_plans.mkdir(parents=True)
    target = proj_plans / "2026-05-01-my-feature.md"
    target.write_text("---\ntype: plan\n---\n")

    from lore_core.plans.router import find_existing_plan_path
    assert find_existing_plan_path(tmp_path, "my-feature") == target
