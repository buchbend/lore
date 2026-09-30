"""Grep coverage for the `build` skill and the writing-rules wiring (PRD 0015).

Skill text is prose, so a grep test is the only mechanical check. Each test
pins one behaviour PRD 0015 § Build, § Ledger and breakpoints and § Writing
rules everywhere asks for, in the file where the skill keeps it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from lore_cli.workflow_cmd import app as workflow_app
from lore_workflow.board_parser import parse_board
from lore_workflow.ledger import parse_board_ledger

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS = REPO_ROOT / "lore-workflow" / "skills"
BUILD = SKILLS / "build"

# Every skill that writes team-facing text (PRD 0015 § Writing rules everywhere).
TEXT_WRITING_SKILLS = ("grilling", "to-epic", "build", "document", "handover", "file-issue")


def _text(rel: str) -> str:
    """Whitespace-joined file text, so wrapped prose matches across lines."""
    return " ".join((SKILLS / rel).read_text(encoding="utf-8").split())


def _section(heading: str) -> str:
    """The body of one `## ` section of build/SKILL.md, whitespace-joined.

    A heading inside a code fence (the board template's `## Ledger`) does not
    end the section.
    """
    lines, found, in_fence = [], False, False
    for line in (BUILD / "SKILL.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
        if not in_fence and line.startswith("## "):
            if found:
                break
            found = line == f"## {heading}"
        if found:
            lines.append(line)
    assert found, f"build/SKILL.md has no section '{heading}'"
    return " ".join(" ".join(lines).split())


@pytest.mark.parametrize("skill", TEXT_WRITING_SKILLS)
def test_text_writing_skill_runs_the_writing_rules(skill: str) -> None:
    assert "lore style show writing-rules" in _text(f"{skill}/SKILL.md")


# --- finish points --------------------------------------------------------


def test_every_finish_point_runs_the_ledger_check() -> None:
    assert "lore workflow ledger-check" in _section("Rules for every mode")
    assert "lore workflow ledger-check" in _section("Mode `issue`")
    assert "lore workflow ledger-check" in _text("build/loop-wrap-up.md")
    assert "lore workflow ledger-check -" in _text("build/epic-tail.md")


def test_loop_and_issue_archive_the_ledger_after_the_check() -> None:
    issue = _section("Mode `issue`")
    wrap_up = _text("build/loop-wrap-up.md")
    for text in (issue, wrap_up):
        assert text.index("ledger-check") < text.index("lore workflow ledger-archive")


def test_each_merged_round_or_feature_writes_a_resume_line() -> None:
    skill = _text("build/SKILL.md")
    assert "--kind resume" in skill
    assert "`/clear` is safe" in skill
    assert "resume line (breakpoint)" in _section("Mode `loop`")
    assert "resume line (breakpoint)" in _section("Mode `epic`")


def test_every_finish_point_writes_the_handover_section() -> None:
    assert "## Handover" in _text("build/SKILL.md")
    assert "handover section" in _section("Mode `issue`")
    assert "handover section" in _text("build/loop-wrap-up.md")
    assert "Write `## Handover` into the epic issue body" in _text("build/epic-tail.md")


def test_the_shipping_pr_carries_the_version_bump_last() -> None:
    assert "tools/release.py --in-branch" in _text("build/SKILL.md")
    for rel in ("build/loop-wrap-up.md", "build/epic-tail.md"):
        assert "version bump as the last commit" in _text(rel), rel


def test_loop_reaches_the_remote_through_one_wrap_up_pr() -> None:
    wrap_up = _text("build/loop-wrap-up.md")
    assert "the one thing that reaches the remote" in wrap_up
    assert "Its body is the handover section" in wrap_up


# --- review ---------------------------------------------------------------


def test_review_depth_follows_the_risk_level() -> None:
    review = _text("build/review.md")
    assert "lore workflow risk <n> --json" in review
    assert "| `low` | `mid` | `code-review <n> low` |" in review
    assert "| `high` | `strong` | `code-review <n> medium` |" in review
    assert "Never lower it" in review


def test_ci_and_ruff_leave_the_verdict_block() -> None:
    review = (BUILD / "review.md").read_text(encoding="utf-8")
    assert "gh pr checks" in review
    block = review[review.index("```\nPR #<n>") : review.index("```", review.index("PR #<n>"))]
    assert "CI" not in block
    assert "ruff" not in block


def test_whole_epic_review_needs_three_features_and_checks_the_docs() -> None:
    tail = _text("build/epic-tail.md")
    assert "three or more features" in tail
    assert "No separate docs round" in tail


# --- board ----------------------------------------------------------------


def test_the_board_template_parses_with_its_ledger_before_the_notes() -> None:
    skill = (BUILD / "SKILL.md").read_text(encoding="utf-8")
    board = re.search(r"```\n(<!-- lore-orchestrate-epic:status v1 -->.*?)```", skill, re.S)
    assert board, "build/SKILL.md lost the board template"
    text = board.group(1)
    assert text.index("## Ledger") < text.index("## Notes")
    assert parse_board(text)[0].state == "queued"
    filled = text.replace("<UTC timestamp>", "2026-09-29T10:00:00Z")
    assert [e.kind for e in parse_board_ledger(filled)] == ["resume"]


# --- document -------------------------------------------------------------


def test_document_marks_the_prd_shipped_as_the_one_sanctioned_edit() -> None:
    text = _text("document/SKILL.md")
    assert "lore workflow prd-ship" in text
    assert "One sanctioned exception" in text


# --- CLI verbs the skills call exist -------------------------------------


def test_every_lore_workflow_verb_in_the_skills_exists() -> None:
    registered = {c.name for c in workflow_app.registered_commands}
    cited: set[str] = set()
    for path in SKILLS.rglob("*.md"):
        cited |= set(re.findall(r"lore workflow ([a-z][a-z-]*)", path.read_text(encoding="utf-8")))
    assert cited, "no skill cites a `lore workflow` verb"
    assert cited <= registered, f"skills cite unknown verbs: {sorted(cited - registered)}"


# --- who merges after a PASS ----------------------------------------------

PASS_ACTIONS = (
    "**PASS and green checks, `epic` feature PR:** merge it into `epic/<issue>`.",
    "**PASS and green checks, the epic PR:** go back to [epic-tail.md](epic-tail.md) § 4.",
    "**PASS and green checks, `issue` mode:** go on to the finish point. The user merges.",
)


def test_a_pass_names_its_mode_before_any_merge() -> None:
    review = _text("build/review.md")
    for line in PASS_ACTIONS:
        assert line in review, line
    assert "PASS and green checks: merge" not in review


def test_issue_mode_never_tells_the_agent_to_merge() -> None:
    """Issue mode ends at the finish point; the user merges its PR."""
    issue = _section("Mode `issue`")
    # "pre-merge" names a mode of `document`, not a merge.
    merges = re.findall(r"(?<!pre-)\bmerg\w*", issue, re.I)
    assert merges == ["merges"], merges
    assert "The user merges." in issue


def test_epic_outcomes_go_through_ledger_set_on_the_board() -> None:
    """`ledger-set` edits a file unless it gets `--board -`; an epic has no file."""
    assert "`--board -`" in _section("Rules for every mode")
    assert "lore workflow ledger-set <n> --outcome approved --board -" in _text(
        "build/epic-tail.md"
    )
    assert "`--board -`" in _text("handover/SKILL.md")
