"""The writing skills write their issue text through `file-issue` (sub-issue #309).

PRD 0009 says to test external behaviour, not skill wording, so nothing here
asserts prose. What is asserted is the machine-consumed part:

- the filing skills point at that skill instead of prescribing filing,
- `to-epic`'s sub-issue template carries the writing rules' own section skeleton,
- and an epic body composed from that template's linkage fields still passes
  the roadmap validator, which is what `orchestrate-epic` reads.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from lore_core.style import default_style_path, default_vale_config_path
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
# The linkage header is one line of bold-labelled fields separated by "·".
# Four short values under four headings pushed the first sentence of content
# past a reader's screen, and no parser ever read those headings.
_LINKAGE = re.compile(r"\*\*([^*]+)\*\*\s*([^·\n]+)")


def _skill_text(name: str) -> str:
    return (SKILLS_ROOT / name / "SKILL.md").read_text(encoding="utf-8")


def _block(text: str, tag: str) -> str:
    match = re.search(rf"<{tag}>\n(.*?)\n</{tag}>", text, re.DOTALL)
    assert match, f"missing <{tag}> block"
    return match.group(1)


def _sections(text: str) -> list[str]:
    return _SECTION.findall(text)


def _required_sections() -> list[str]:
    """The section list the resolved writing rules require, read from the
    document itself rather than restated here — an override changes both."""
    rules = default_style_path("writing-rules").read_text(encoding="utf-8")
    match = re.search(r"## Required issue structure\n+```\n(.*?)```", rules, re.DOTALL)
    assert match, "the writing rules lost the 'Required issue structure' block"
    return _sections(match.group(1))


def _linkage(text: str) -> dict[str, str]:
    """The linkage header's fields, keyed by label."""
    line = next((ln for ln in text.splitlines() if ln.startswith("**Epic**")), None)
    assert line, "sub-issue template has no one-line linkage header"
    return {label.strip(): value.strip() for label, value in _LINKAGE.findall(line)}


def _field(body: str, label: str) -> str:
    """A linkage field's value, read from the rendered header line."""
    fields = _linkage(body)
    assert label in fields, f"rendered sub-issue has no '{label}' linkage field"
    return fields[label]


def _render_sub_issue(values: dict[str, str]) -> str:
    """Render a sub-issue from the template's own linkage labels and section
    list, so anything dropped from the template is missing from the render."""
    template = _block(_skill_text("to-epic"), "sub-issue-template")
    header = " · ".join(f"**{label}** {values.get(label, 'TODO')}" for label in _linkage(template))
    body = "\n\n".join(
        f"## {name}\n{values.get(name, 'Filled by the drafting session.')}"
        for name in _sections(template)
    )
    return f"{header}\n\n{body}"


@pytest.mark.parametrize("skill", FILING_SKILLS)
def test_filing_skills_point_at_file_issue(skill: str) -> None:
    assert "../file-issue/SKILL.md" in _skill_text(skill), (
        f"{skill}/SKILL.md must route its filing through file-issue"
    )


def test_sub_issue_template_carries_the_writing_rules_skeleton() -> None:
    template = _block(_skill_text("to-epic"), "sub-issue-template")
    present = _sections(template)
    missing = [s for s in _required_sections() if s not in present]
    assert not missing, f"sub-issue template is missing required sections: {missing}"


def test_sub_issue_template_keeps_the_epic_linkage_header() -> None:
    fields = _linkage(_block(_skill_text("to-epic"), "sub-issue-template"))
    missing = [f for f in ("Epic", *ROADMAP_FIELDS) if f not in fields]
    assert not missing, f"sub-issue template is missing linkage fields: {missing}"


def test_the_linkage_header_spends_no_headings() -> None:
    """A reader scans the issue's content, so the linkage costs one line."""
    present = _sections(_block(_skill_text("to-epic"), "sub-issue-template"))
    stray = [s for s in ("Epic", *ROADMAP_FIELDS) if s in present]
    assert not stray, f"linkage fields belong in the header line, not headings: {stray}"


def test_the_collapsed_labels_are_retired_headings() -> None:
    """The Vale check is what catches a draft written from the pre-collapse
    template, so the rule lists every label the header absorbed."""
    rule = default_vale_config_path().parent / "WritingRules" / "RetiredHeading.yml"
    retired = yaml.safe_load(rule.read_text(encoding="utf-8"))["raw"][0]
    labels = _linkage(_block(_skill_text("to-epic"), "sub-issue-template"))
    missing = [label for label in labels if label not in retired]
    assert not missing, f"the retired-heading rule must list: {missing}"


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
