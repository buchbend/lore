"""The workflow prose uses plain verbs for branch creation and merging.

"Cut" (a branch) and "land" (a pull request) are insider verbs that need a
glossary entry before a reader can resolve them. "Funnel" is a metaphor a reader
has to map onto the thing before the sentence means anything. Issue-register
rule 3 asks for the shorter common word, so the prose says "create", "merge",
and plainly what `file-issue` does, and the glossary carries no entry for the
two verbs.

Machine-read formats are exempt from the register and are not checked here.
Idiomatic non-git uses ("the decision the brief lands on") are also out of
scope: they name neither action.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS = sorted((REPO_ROOT / "lore-workflow" / "skills").glob("*/SKILL.md"))
HOW_TOS = sorted((REPO_ROOT / "docs" / "how-to").glob("*.md"))
CONVENTIONS = REPO_ROOT / "docs" / "conventions.md"

# The git-sense uses, matched tightly enough that "shortcut", "cut the corner"
# and "cut the fewest slices" (carving work, not opening a branch) stay legal.
BRANCH_CUT = re.compile(
    r"\bre-cut\b|\bcut(?:s|ting)?\s+(?:and\s+push\s+)?(?:the\s+|a\s+|an\s+)?"
    r"(?:epic\s+)?branch\b|\bis\s+cut\s+from\b|\bcut\s+and\s+push\b",
    re.IGNORECASE,
)
# A metaphor for the skill that writes and files issue text. Say what it does.
FUNNEL = re.compile(r"\bfunnels?\b", re.IGNORECASE)
MERGE_LAND = re.compile(
    r"\bpre-land\b|\bpost-land\b|\bLand\s+checklist\b|"
    r"\blands?\s+(?:the\s+)?(?:epic|work|PR|pull request)\b|"
    r"\bmust\s+land\b|\bland\s+in\s+one\s+PR\b|\blands\s+in\s+\*\*one\s+PR\*\*",
    re.IGNORECASE,
)


def _offenders(text: str) -> list[str]:
    return [
        m.group(0) for pattern in (BRANCH_CUT, MERGE_LAND, FUNNEL) for m in pattern.finditer(text)
    ]


@pytest.mark.parametrize(
    "path", SKILLS + HOW_TOS + [CONVENTIONS], ids=lambda p: p.parent.name + "/" + p.name
)
def test_workflow_prose_uses_plain_verbs(path: Path):
    found = _offenders(path.read_text(encoding="utf-8"))
    assert not found, (
        f"{path.relative_to(REPO_ROOT)} uses insider wording: {sorted(set(found))!r}. "
        f"Say 'create', 'merge', and what the skill does — see #324 and #327."
    )


def test_conventions_glossary_does_not_define_cut_or_land():
    text = CONVENTIONS.read_text(encoding="utf-8")
    entries = re.findall(r"^- \*\*(cut|land)\*\*", text, re.IGNORECASE | re.MULTILINE)
    assert not entries, (
        f"docs/conventions.md still defines {entries!r} in its glossary. The plain "
        f"verbs need no entry — remove them rather than document the jargon (#324)."
    )


def test_wiring_test_and_readme_describe_file_issue_plainly():
    """The skill table and the wiring test carry the same plain wording."""
    for rel in ("lore-workflow/README.md", "tests/test_workflow_issue_register_wiring.py"):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        found = FUNNEL.findall(text)
        assert not found, (
            f"{rel} calls file-issue a {sorted(set(found))!r}. Say what it does "
            f"— it writes issue text and files it (#327)."
        )
