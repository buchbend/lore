"""`file-issue` reads the target repo's glossary while drafting (sub-issue #341).

The writing rules already tell a writer to take terms from the glossary; no
skill read one before this. `file-issue` step 1 now reads `CONTEXT.md` in the
same step it resolves the writing rules, drafts without one when the repo
holds none, and never writes to the file — that stays `domain-modeling`'s
write path (see `test_workflow_glossary_write_gate.py`).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FILE_ISSUE_SKILL = REPO_ROOT / "lore-workflow" / "skills" / "file-issue" / "SKILL.md"
DIRECTIVE_TEMPLATE = (
    REPO_ROOT / "lib" / "lore_core" / "templates" / "integration-rules" / "default.md"
)


def _step_one() -> str:
    """The numbered step 1 body, split on the next numbered step boundary.

    A three-hash subheading (``### When a fact is missing``) doesn't match
    the two-hash-plus-digit pattern, so it stays attached to whichever
    numbered step it lives under.
    """
    text = FILE_ISSUE_SKILL.read_text(encoding="utf-8")
    sections = re.split(r"\n(?=## \d+\. )", text)
    for section in sections:
        if section.startswith("## 1."):
            return section
    raise AssertionError("file-issue lost its numbered step 1")


def test_step_one_reads_the_glossary_alongside_the_writing_rules() -> None:
    step = _step_one()
    assert "writing-rules" in step
    assert "CONTEXT.md" in step


def test_missing_glossary_drafts_anyway_and_names_the_absence() -> None:
    step = _step_one().lower()
    assert "draft without one" in step
    assert "one line" in step


def test_skill_never_writes_to_the_glossary() -> None:
    step = _step_one()
    assert "never write to `context.md`" in step.lower()
    assert "grilling" in step


def test_directive_names_the_glossary_in_one_line() -> None:
    lines = [
        line for line in DIRECTIVE_TEMPLATE.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    glossary_lines = [line for line in lines if "CONTEXT.md" in line]
    assert len(glossary_lines) == 1, glossary_lines
    assert "writing-rules" in glossary_lines[0]
