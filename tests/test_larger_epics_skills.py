"""Skill text for larger epics (PRD 0024, ADR 0023): split reasons, substeps, review plan."""

from __future__ import annotations

from pathlib import Path

SKILLS = Path(__file__).resolve().parent.parent / "skills"


def _text(rel: str) -> str:
    raw = (SKILLS / rel).read_text(encoding="utf-8").replace("\n>", "\n")
    return " ".join(raw.split())


def test_to_epic_names_the_three_split_reasons_and_soft_limit() -> None:
    text = _text("to-epic/SKILL.md")
    assert "ADR 0023" in text
    assert "about 12 rows" in text
    assert "wait until another part is deployed or published" in text
    assert "decision by the user" in text
    assert "share no release" in text
    assert "more than 6 smells like two epics" not in text


def test_orient_and_build_do_not_send_multi_topic_work_back() -> None:
    assert "Several features, or the shape is unsettled" not in _text("orient/SKILL.md")
    assert "Several features, or unshaped work" not in _text("build/SKILL.md")


def test_sub_issue_template_has_optional_substeps() -> None:
    assert "## Substeps" in _text("to-epic/SKILL.md")


def test_teammate_brief_asks_one_commit_per_substep() -> None:
    text = _text("build/teammate-brief.md")
    assert "one commit per substep" in text
    assert "substep number in the commit subject" in text
    assert "one ticked box per landed commit" in text
