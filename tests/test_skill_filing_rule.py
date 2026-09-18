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


def test_file_issue_creates_the_label_when_missing() -> None:
    text = _text("file-issue")
    assert "gh label create agent-filed" in text


def test_file_issue_comments_open_naming_themselves_agent_filed() -> None:
    text = _text("file-issue")
    assert "opens with a line naming yourself as agent-filed" in text


# --- decision gate ---------------------------------------------------------

DECISION_GATE = "outside a grilling or domain-modeling session is a PR. A human merges it."


@pytest.mark.parametrize("skill", ["implement-issue", "tdd", "orchestrate-epic"])
def test_decision_gate_present(skill: str) -> None:
    text = _text(skill)
    assert DECISION_GATE in text, f"{skill}/SKILL.md is missing the decision gate"


# --- session-end list -------------------------------------------------------

SESSION_END_LIST = "issue and PR the session created or commented on"


@pytest.mark.parametrize("skill", ["orient", "implement-issue", "tdd"])
def test_session_end_list_present(skill: str) -> None:
    text = _text(skill)
    assert SESSION_END_LIST in text, f"{skill}/SKILL.md is missing the session-end list"


# --- retrieval-miss check ---------------------------------------------------


@pytest.mark.parametrize("skill", ["orient", "implement-issue", "tdd"])
def test_retrieval_miss_check_present(skill: str) -> None:
    text = _text(skill)
    assert "feedback.retrieval_misses" in text
    assert "feedback.retrieval_misses_repo" in text
    assert "retrieval miss" in text.lower()


@pytest.mark.parametrize("skill", ["orient", "implement-issue", "tdd"])
def test_retrieval_miss_check_names_the_skip_condition(skill: str) -> None:
    """AC: while the flag is false, the skills instruct the agent to skip the check."""
    text = _text(skill).lower()
    assert "when false, skip the check" in text
