"""A flag carries the same writing rules as issue and PR text.

A flag lands on a team surface and a teammate reads it cold, months later,
without the session that filed it. That is the reader the writing rules are
written for, so flag text follows them.

Nothing here asserts phrasing. What is asserted is that every surface a session
reads while composing a flag names the rules, and that the numbers those
surfaces quote come from the rules document rather than from memory:

- the rules document itself names flag text in its scope and says which of its
  sections a two-field flag skips,
- the `lore_flag` tool schema repeats the sentence ceilings,
- the SessionStart directive names flag text beside issue and PR text.
"""

from __future__ import annotations

import re

from lore_core.session_start import load_directive_lines
from lore_core.style import default_style_path
from lore_mcp.server import _tool_schema

# Rule 6, read from the document so an edit to the ceilings moves both sides.
_CEILINGS = re.compile(
    r"Maximum (\d+) words for an instruction\. Maximum (\d+) for a description\."
)


def _rules_text() -> str:
    return default_style_path("writing-rules").read_text(encoding="utf-8")


def _ceilings() -> tuple[str, str]:
    match = _CEILINGS.search(_rules_text())
    assert match, "the writing rules lost their sentence-length rule"
    return match.group(1), match.group(2)


def _flag_tool() -> dict:
    tool = next((t for t in _tool_schema() if t["name"] == "lore_flag"), None)
    assert tool, "the MCP server no longer exposes lore_flag"
    return tool


def _flag_field(name: str) -> str:
    return _flag_tool()["inputSchema"]["properties"][name]["description"]


def test_the_rules_scope_names_flag_text() -> None:
    scope = next(
        (line for line in _rules_text().splitlines() if line.startswith("Scope:")),
        "",
    )
    assert "flag" in scope.lower(), f"flag text is outside the rules' scope line: {scope!r}"


def test_the_rules_say_which_sections_a_flag_skips() -> None:
    """A flag holds a lead and a body, so the issue skeleton and EARS do not
    apply to it. Without the carve-out a session drafts an issue into a flag."""
    section = re.search(r"^## Flag text$(.*?)(?=^## )", _rules_text(), re.M | re.S)
    assert section, "the writing rules hold no 'Flag text' section"
    body = section.group(1)
    assert "EARS" in body
    assert "skeleton" in body


def test_the_flag_tool_repeats_the_sentence_ceilings() -> None:
    instruction, description = _ceilings()
    assert instruction in _flag_field("lead"), (
        f"the lead field does not carry the {instruction}-word ceiling"
    )
    assert description in _flag_field("body"), (
        f"the body field does not carry the {description}-word ceiling"
    )


def test_the_flag_tool_names_the_writing_rules() -> None:
    assert "writing rules" in _flag_tool()["description"].lower()


def test_the_directive_names_flag_text_beside_issue_text() -> None:
    line = next(
        (ln for ln in load_directive_lines() if "lore style show writing-rules" in ln),
        "",
    )
    assert "flag" in line.lower(), f"the directive's rules line omits flag text: {line!r}"
