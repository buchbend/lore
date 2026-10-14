"""Grep coverage for the filing-rule instructions the skills carry.

ADR 0012 moves every agent-filed fact to a repo artifact. PRD 0014 names four
prose instructions the skills carry: the agent-filed marker, the decision
gate, the session-end list, and the retrieval-miss check. Skill text is
prose, so a grep test is the only mechanical check available, matching the
shape of tests/test_writing_rules.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS = REPO_ROOT / "lore-workflow" / "skills"


def _text(skill: str) -> str:
    """Whitespace-joined file content — wrapped prose must match across line breaks,
    the same normalization tests/test_writing_rules.py uses for skill/frontmatter text."""
    return " ".join((SKILLS / skill / "SKILL.md").read_text(encoding="utf-8").split())


# --- file-issue: agent-filed marker --------------------------------------


def test_file_issue_labels_every_issue_agent_filed() -> None:
    text = _text("file-issue")
    assert "`agent-filed`" in text
    assert "label" in text.lower()
    assert "--label agent-filed" in text, "the gh issue create command must carry the flag itself"


def test_file_issue_creates_the_label_when_missing() -> None:
    text = _text("file-issue")
    assert "gh label create agent-filed" in text


def test_file_issue_comments_open_naming_themselves_agent_filed() -> None:
    text = _text("file-issue")
    assert "opens with a line naming yourself as agent-filed" in text


def test_file_issue_closes_only_own_issues() -> None:
    text = _text("file-issue")
    assert "Close only an issue you opened yourself" in text
    assert "never change the state of a human's issue or PR" in text


# --- decision gate ---------------------------------------------------------

DECISION_GATE = "outside a grilling session is a PR. A human approves its merge."


@pytest.mark.parametrize("skill", ["build", "tdd"])
def test_decision_gate_present(skill: str) -> None:
    text = _text(skill)
    assert DECISION_GATE in text, f"{skill}/SKILL.md is missing the decision gate"


# --- session-end list -------------------------------------------------------

SESSION_END_LIST = "issue and PR the session created or commented on"


@pytest.mark.parametrize("skill", ["orient", "build", "tdd"])
def test_session_end_list_present(skill: str) -> None:
    text = _text(skill)
    assert SESSION_END_LIST in text, f"{skill}/SKILL.md is missing the session-end list"


# --- retrieval-miss check ---------------------------------------------------


@pytest.mark.parametrize("skill", ["orient", "build", "tdd"])
def test_retrieval_miss_check_present(skill: str) -> None:
    text = _text(skill)
    assert "feedback.retrieval_misses" in text
    assert "feedback.retrieval_misses_repo" in text
    assert "retrieval miss" in text.lower()


@pytest.mark.parametrize("skill", ["orient", "build", "tdd"])
def test_retrieval_miss_check_names_the_skip_condition(skill: str) -> None:
    """AC: while the flag is false, the skills instruct the agent to skip the check."""
    text = _text(skill).lower()
    assert "when false, skip the check" in text


# --- the filing rule: an issue or the roadmap -------------------------------
# PRD 0018 (the roadmap) § Skills: a defect or a concrete change goes to an
# issue, a feature idea outside the run's scope goes to the roadmap.


def _file(rel: str) -> str:
    """Whitespace-joined text of a file under skills/, blockquote markers dropped."""
    lines = (SKILLS / rel).read_text(encoding="utf-8").splitlines()
    return " ".join(" ".join(line.lstrip("> ") for line in lines).split())


def test_build_sends_a_defect_to_an_issue_and_a_feature_idea_to_the_roadmap() -> None:
    text = _text("build")
    assert "A defect or a concrete change becomes an issue" in text
    assert "A feature idea outside the run's scope goes to the roadmap" in text
    assert "`lore roadmap add` on the build branch" in text


def test_a_teammate_files_a_feature_idea_through_its_pr() -> None:
    text = _file("build/teammate-brief.md")
    assert "A feature idea outside your scope goes to the roadmap" in text
    assert "`lore roadmap add`" in text


def test_loop_wrap_up_sends_a_kept_feature_idea_to_the_roadmap() -> None:
    text = _file("build/loop-wrap-up.md")
    assert "add a feature idea to the roadmap" in text
    assert "`lore roadmap add`" in text
