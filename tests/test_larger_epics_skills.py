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


def test_to_epic_writes_review_plan_and_tail_runs_each_lens_once() -> None:
    assert "## Review plan" in _text("to-epic/SKILL.md")
    tail = _text("build/epic-tail.md")
    assert "Lenses" in tail and "once" in tail and "parallel" in tail


def test_lens_review_range_is_the_rows_own_changes() -> None:
    tail = _text("build/epic-tail.md")
    assert "epic/<issue>..feat/<n>-<slug>" not in tail
    assert "git merge-base" not in tail
    assert "git diff $M^1 $M" in tail
    assert "$M^1..$M^2" not in tail
    assert "§ Lens review" in tail


def test_review_plan_follows_the_checklist_in_the_epic_template() -> None:
    text = _text("to-epic/SKILL.md")
    assert text.index("- [ ] owner/other#7") < text.index("## Review plan")
    assert "- **security:**" in text


def test_to_epic_passes_an_intent_to_the_context_pack() -> None:
    # Without an intent or a focus issue the pack holds no PRD (#646).
    text = _text("to-epic/SKILL.md")
    assert "`lore_context_pack` with `intent`" in text


def test_review_md_has_a_lens_review_section() -> None:
    text = _text("build/review.md")
    assert "and for each lens review" in text
    section = text[text.index("## Lens review") :]
    assert "`strong`" in section and "verdict: PASS | FAIL" in section
    assert "no `risk`" in section and "reviewer tier" in section


def test_polish_to_epic_brief_orient() -> None:
    assert "ADR 0023 (epics split on release order, not topic)" in _text("to-epic/SKILL.md")
    assert "(only for a slice with ordered steps)" in _text("to-epic/SKILL.md")
    orient = _text("orient/SKILL.md")
    assert "the pack is thin" not in orient
    assert "restatement" in orient
