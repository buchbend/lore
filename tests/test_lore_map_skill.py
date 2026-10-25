"""Grep coverage for the `/lore:map` plugin skill (PRD 0019, the system map).

Skill text is prose, so a grep test is the only mechanical check. Each test
pins one acceptance criterion of issue 533 (scan the checkouts and keep the
system map true).
"""

from __future__ import annotations

from pathlib import Path

import yaml

SKILL = Path(__file__).resolve().parent.parent / "skills" / "map" / "SKILL.md"


def _text() -> str:
    return " ".join(SKILL.read_text(encoding="utf-8").split())


def _section(heading: str) -> str:
    raw = SKILL.read_text(encoding="utf-8")
    body = raw.split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0]
    return " ".join(body.split())


def test_frontmatter_names_the_skill_and_makes_it_invocable() -> None:
    raw = SKILL.read_text(encoding="utf-8")
    fm = yaml.safe_load(raw[4 : raw.find("\n---", 3)])
    assert fm["name"] == "lore:map"
    assert fm["user_invocable"] is True


def test_map_phase_runs_the_scan_and_opens_one_wiki_pr_with_three_sections() -> None:
    text = _section("Map phase")
    assert "lore map scan --json" in text
    assert "one wiki PR per run" in text
    for name in ("### Added", "### Changed", "### No longer found"):
        assert name in text


def test_map_phase_keeps_edges_the_scan_no_longer_finds() -> None:
    text = _section("Map phase")
    assert "Leave each edge the scan no longer finds in the map" in text
    assert "A human deletes it in the PR" in text


def test_a_repo_without_a_checkout_is_reported_and_its_edges_stay() -> None:
    text = _section("Map phase")
    assert "`no_checkout`" in text
    assert "keep its edges" in text


def test_tidy_phase_waits_for_the_merged_map_pr() -> None:
    text = _section("Tidy phase")
    assert "While the map PR is open, do not start the tidy phase" in text


def test_tidy_phase_keeps_repo_rules_and_borderline_text() -> None:
    text = _section("Tidy phase")
    assert "Keep the repo's commands, conventions and release steps" in text
    assert "If a passage may be a repo rule, keep the passage" in text
    assert "Name each kept borderline passage in the PR body" in text
    assert "One PR per repo" in text


def test_tidy_switch_skips_the_phase() -> None:
    text = _text()
    assert "scopes.<name>.map.tidy: false" in text
    assert "`tidy` is `false`, skip the tidy phase" in text


def test_the_skill_writes_only_through_prs() -> None:
    text = _text()
    assert "Never push to a default branch" in text


def test_an_ambiguous_package_is_resolved_from_the_consumer_not_guessed() -> None:
    text = _section("Map phase")
    assert "`ambiguous`" in text
    assert "Never pick one producer for an ambiguous package by guess" in text
