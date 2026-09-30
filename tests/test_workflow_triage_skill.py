"""The `triage` skill's contract: review only, fixed verdicts, full coverage."""

from __future__ import annotations

from pathlib import Path

TRIAGE = Path(__file__).resolve().parent.parent / "lore-workflow" / "skills" / "triage"

VERDICTS = (
    "**Critical**",
    "**Do next**",
    "**Backlog**",
    "**Optional**",
    "**Close: done**",
    "**Close: drop / move / duplicate**",
)


def _skill() -> str:
    return (TRIAGE / "SKILL.md").read_text(encoding="utf-8")


def test_triage_is_review_only_until_the_user_decides() -> None:
    text = _skill()
    assert "Do not comment, label, close, commit or push until the user says so." in text
    assert "act only on what they approved" in text


def test_triage_names_the_six_verdicts_in_order() -> None:
    text = _skill()
    positions = [text.index(v) for v in VERDICTS]
    assert positions == sorted(positions)


def test_triage_checks_that_every_open_issue_appears_once() -> None:
    text = _skill()
    assert "comm -3 open.txt seen.txt" in text
    assert "uniq -d" in text


def test_triage_page_spec_ships_beside_the_skill() -> None:
    assert "[PAGE.md](PAGE.md)" in _skill()
    page = (TRIAGE / "PAGE.md").read_text(encoding="utf-8")
    for section in (
        "Critical: act this week",
        "Risks no issue tracks",
        "Open PRs",
        "Questions for you",
    ):
        assert section in page, section
