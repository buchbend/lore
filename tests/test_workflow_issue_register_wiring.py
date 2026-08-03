"""The writing skills write their issue text through `file-issue` (sub-issue #309).

PRD 0009 says to test external behaviour, not skill wording, so nothing here
asserts prose. What is asserted is the machine-consumed part:

- the filing skills point at that skill instead of prescribing filing,
- `to-epic`'s sub-issue template carries the register's own section skeleton,
- and an epic body composed from that template's linkage fields still passes
  the roadmap validator, which is what `orchestrate-epic` reads.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from lore_core.style import default_style_path
from lore_workflow import roadmap_validator

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_ROOT = REPO_ROOT / "lore-workflow" / "skills"

# Every skill that writes issue or PR text. `file-issue` itself does the writing.
FILING_SKILLS = (
    "to-epic",
    "seed-epic",
    "orchestrate-epic",
    "implement-issue",
    "brief",
    "document-epic",
)

# Linkage fields the roadmap table's columns are built from. Losing one of
# these from the sub-issue template leaves `to-epic` without the data the
# validator requires.
ROADMAP_FIELDS = ("Repo", "Type", "Blocked by")

_SECTION = re.compile(r"^## (.+)$", re.M)


def _skill_text(name: str) -> str:
    return (SKILLS_ROOT / name / "SKILL.md").read_text(encoding="utf-8")


def _block(text: str, tag: str) -> str:
    match = re.search(rf"<{tag}>\n(.*?)\n</{tag}>", text, re.DOTALL)
    assert match, f"missing <{tag}> block"
    return match.group(1)


def _sections(text: str) -> list[str]:
    return _SECTION.findall(text)


def _register_skeleton() -> list[str]:
    """The section list the resolved register requires, read from the register
    itself rather than restated here — an override changes both together."""
    register = default_style_path("issue-register").read_text(encoding="utf-8")
    match = re.search(r"## Required issue structure\n+```\n(.*?)```", register, re.DOTALL)
    assert match, "the register lost its 'Required issue structure' block"
    return _sections(match.group(1))


def _field(body: str, heading: str) -> str:
    """First non-empty line under ``## <heading>``."""
    match = re.search(rf"^## {re.escape(heading)}$\n+(.+)$", body, re.M)
    assert match, f"rendered sub-issue has no '## {heading}' section"
    return match.group(1).strip()


def _render_sub_issue(values: dict[str, str]) -> str:
    """Render a sub-issue from the template's own section list, so a section
    dropped from the template is a section missing from the render."""
    sections = _sections(_block(_skill_text("to-epic"), "sub-issue-template"))
    return "\n\n".join(
        f"## {name}\n{values.get(name, 'Filled by the drafting session.')}" for name in sections
    )


@pytest.mark.parametrize("skill", FILING_SKILLS)
def test_filing_skills_point_at_file_issue(skill: str) -> None:
    assert "../file-issue/SKILL.md" in _skill_text(skill), (
        f"{skill}/SKILL.md must route its filing through file-issue"
    )


def test_sub_issue_template_carries_the_register_skeleton() -> None:
    template = _block(_skill_text("to-epic"), "sub-issue-template")
    present = _sections(template)
    missing = [s for s in _register_skeleton() if s not in present]
    assert not missing, f"sub-issue template is missing register sections: {missing}"


def test_sub_issue_template_keeps_the_epic_linkage_header() -> None:
    present = _sections(_block(_skill_text("to-epic"), "sub-issue-template"))
    missing = [s for s in ("Epic", *ROADMAP_FIELDS) if s not in present]
    assert not missing, f"sub-issue template is missing linkage sections: {missing}"


def test_epic_body_composed_from_the_template_passes_the_validator() -> None:
    """The acceptance criterion: compose the roadmap table out of what the new
    sub-issue template carries, and the validator accepts it."""
    subs = [
        _render_sub_issue(
            {
                "Epic": "ccatobs/widget#11",
                "Repo": "widget",
                "Type": "AFK",
                "Blocked by": blocked,
            }
        )
        for blocked in ("None — can start immediately.", "#12", "#12, #13")
    ]
    rows = []
    for number, sub in enumerate(subs, start=12):
        blocked = _field(sub, "Blocked by")
        rows.append(
            f"| {number - 11} | Feature {number - 11} | ccatobs/widget#{number} "
            f"| {_field(sub, 'Repo')} | {_field(sub, 'Type')} "
            f"| {'—' if blocked.startswith('None') else blocked} |"
        )
    epic_body = "\n".join(
        [
            "## Roadmap",
            "",
            "| # | Feature | Issue | Repo | Type | Blocked by |",
            "|---|---------|-------|------|------|------------|",
            *rows,
        ]
    )
    result = roadmap_validator.validate_roadmap(epic_body)
    assert result.ok, f"composed epic body must validate; problems={result.problems}"
    assert len(result.rows) == 3
