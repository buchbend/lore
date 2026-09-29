"""Glossary write gate: human-approved, grilling-only (sub-issue #338).

A short name that means a piece of work (`P6`, `G4`) reads exactly like a
real data level (`L0`). `domain-modeling` used to invite any skill to
maintain the domain model — that invitation is the open write path this
closes. Its rules now live in `grilling` (PRD 0015). `grilling` recaps
every term it wrote or changed, so the user sees the glossary diff without
opening `CONTEXT.md`.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GRILLING_SKILL = REPO_ROOT / "lore-workflow" / "skills" / "grilling" / "SKILL.md"


def _frontmatter_description(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("description:"):
            return line.removeprefix("description:").strip()
    raise AssertionError("no description line in frontmatter")


def test_grilling_description_does_not_invite_other_skills() -> None:
    description = _frontmatter_description(GRILLING_SKILL.read_text(encoding="utf-8"))
    assert "another skill" not in description
    assert "maintain the domain model" not in description


def test_grilling_states_a_person_approves_every_entry() -> None:
    body = GRILLING_SKILL.read_text(encoding="utf-8")
    assert "approves" in body
    assert "before it is written" in body


def test_grilling_recaps_written_terms() -> None:
    body = GRILLING_SKILL.read_text(encoding="utf-8").lower()
    assert "list every term" in body


def test_grilling_recap_covers_the_empty_case() -> None:
    body = GRILLING_SKILL.read_text(encoding="utf-8").lower()
    assert "printing an empty list" in body


def test_build_routes_adr_candidates_through_grillings_criteria() -> None:
    text = (REPO_ROOT / "lore-workflow" / "skills" / "build" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "../grilling/ADR-FORMAT.md" in text


# The one sentence every drafting skill carries. Pinning it whole stops the
# destination and the wording drifting apart across skills.
GLOSSARY_GATE = (
    "**never write to `context.md`** — a term worth adding belongs to `grilling`, not this skill."
)


def test_no_drafting_skill_routes_a_new_term_anywhere_but_grilling() -> None:
    for skill in ("orient", "file-issue"):
        text = (REPO_ROOT / "lore-workflow" / "skills" / skill / "SKILL.md").read_text(
            encoding="utf-8"
        )
        # Joined on whitespace: the sentence wraps across lines in some skills.
        assert GLOSSARY_GATE in " ".join(text.split()).lower(), skill
