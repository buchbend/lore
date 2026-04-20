"""Pure CLAUDE.md `## Lore` block parser.

Extracted from `lore_cli.attach_cmd` so `lore_core` modules (e.g.
`scope_resolver`) can read attach blocks without depending on the CLI
layer. The CLI module re-exports these for backward compatibility.

Read-only. Writers stay in `lore_cli.attach_cmd`.
"""

from __future__ import annotations

import re
from pathlib import Path

SECTION_HEADING = "## Lore"
LORE_KEYS: tuple[str, ...] = ("wiki", "scope", "backend", "issues", "prs")
BULLET_RE = re.compile(r"^- ([A-Za-z][\w-]*): ?(.*)$")
HEADING_RE = re.compile(r"^## (.+?)\s*$")


def _split_lines(text: str) -> tuple[list[str], bool]:
    """Split text into lines, remembering whether it ended with a newline."""
    if not text:
        return [], False
    trailing = text.endswith("\n")
    body = text[:-1] if trailing else text
    return body.split("\n"), trailing


def find_section(lines: list[str]) -> tuple[int, int] | None:
    """Return (start, end) of the `## Lore` section, or None if absent.

    `start` is the heading line index; `end` is exclusive (next `## `
    heading or `len(lines)`).
    """
    start = None
    for i, line in enumerate(lines):
        if line.strip() == SECTION_HEADING:
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        m = HEADING_RE.match(lines[j])
        if m and m.group(1).strip() != "Lore":
            end = j
            break
    return start, end


def parse_section_body(body_lines: list[str]) -> dict[str, str]:
    """Extract `- key: value` bullets from a section body as a dict."""
    out: dict[str, str] = {}
    for line in body_lines:
        m = BULLET_RE.match(line)
        if m:
            key, value = m.group(1), m.group(2)
            out[key] = value
    return out


def read_attach(path: Path) -> dict[str, str]:
    """Return the parsed Lore block as a dict, or {} if absent."""
    if not path.exists():
        return {}
    lines, _ = _split_lines(path.read_text())
    bounds = find_section(lines)
    if bounds is None:
        return {}
    start, end = bounds
    return parse_section_body(lines[start + 1 : end])
