"""Glossary write gate: human-approved, grilling-only (sub-issue #338).

A short name that means a piece of work (`P6`, `G4`) reads exactly like a
real data level (`L0`). `domain-modeling` used to invite any skill to
maintain the domain model — that invitation is the open write path this
closes. `grilling` now recaps every term it wrote or changed so the user
sees the glossary diff without opening `CONTEXT.md`.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOMAIN_MODELING_SKILL = REPO_ROOT / "lore-workflow" / "skills" / "domain-modeling" / "SKILL.md"
GRILLING_SKILL = REPO_ROOT / "lore-workflow" / "skills" / "grilling" / "SKILL.md"


def _frontmatter_description(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("description:"):
            return line.removeprefix("description:").strip()
    raise AssertionError("no description line in frontmatter")


def test_domain_modeling_description_does_not_invite_other_skills() -> None:
    description = _frontmatter_description(DOMAIN_MODELING_SKILL.read_text(encoding="utf-8"))
    assert "another skill" not in description
    assert "maintain the domain model" not in description


def test_domain_modeling_states_a_person_approves_every_entry() -> None:
    body = DOMAIN_MODELING_SKILL.read_text(encoding="utf-8")
    assert "approves" in body
    assert "before it is written" in body


def test_grilling_recaps_written_terms() -> None:
    body = GRILLING_SKILL.read_text(encoding="utf-8").lower()
    assert "list every term" in body


def test_grilling_recap_covers_the_empty_case() -> None:
    body = GRILLING_SKILL.read_text(encoding="utf-8").lower()
    assert "printing an empty list" in body


def test_implement_issue_and_brief_still_reference_domain_modeling() -> None:
    for skill in ("implement-issue", "brief"):
        text = (REPO_ROOT / "lore-workflow" / "skills" / skill / "SKILL.md").read_text(
            encoding="utf-8"
        )
        assert "domain-modeling" in text


def test_brief_never_writes_to_the_glossary() -> None:
    body = (
        (REPO_ROOT / "lore-workflow" / "skills" / "brief" / "SKILL.md")
        .read_text(encoding="utf-8")
        .lower()
    )
    assert "never write to `context.md`" in body
    assert "grilling" in body
