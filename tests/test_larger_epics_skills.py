"""Skill text for larger epics (PRD 0024, ADR 0023): split reasons, substeps, review plan."""

from __future__ import annotations

from pathlib import Path

SKILLS = Path(__file__).resolve().parent.parent / "skills"


def _text(rel: str) -> str:
    return " ".join((SKILLS / rel).read_text(encoding="utf-8").split())


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
