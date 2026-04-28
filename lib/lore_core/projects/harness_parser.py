"""Extract project-note sections from harness meta-files.

Each function reads a string and returns extracted text (or None /
empty). All functions are pure — no I/O — so callers can pre-read
files in whatever order they prefer and feed the contents in.

The README description algorithm in :func:`extract_description` is
the most opinionated piece. Six branches, each tested individually
in ``tests/test_project_stub.py``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

# ---------------------------------------------------------------------------
# README description algorithm
# ---------------------------------------------------------------------------

#: Minimum prose length to qualify as the project description.
_DESCRIPTION_MIN_CHARS = 40

#: Maximum length; longer descriptions get truncated with an ellipsis.
_DESCRIPTION_MAX_CHARS = 200

#: Lines composed entirely of inline images / links (badge rows).
_BADGE_LINE_RE = re.compile(
    r"""^\s*
    (?:!?\[ [^\]]* \] \( [^)]* \) \s*)+
    \s*$""",
    re.VERBOSE,
)

_HTML_OPEN_RE = re.compile(r"^\s*<")

_ATX_HEADING_RE = re.compile(r"^\s*#{1,6}\s+")


def extract_description(
    *,
    readme: str | None = None,
    pyproject_text: str | None = None,
    package_json_text: str | None = None,
    fallback_repo_slug: str = "project",
) -> str:
    """Compute the project's one-line description with a fallback chain.

    Pipeline (first non-empty wins):

    1. README first paragraph of plain prose ≥40 chars.
    2. ``pyproject.toml`` ``[project] description``.
    3. ``package.json`` ``description``.
    4. Literal ``"Project: <repo-slug>"``.
    """
    if readme:
        readme_desc = _readme_first_paragraph(readme)
        if readme_desc:
            return _cap(readme_desc)

    if pyproject_text:
        py_desc = _pyproject_description(pyproject_text)
        if py_desc:
            return _cap(py_desc)

    if package_json_text:
        pj_desc = _package_json_description(package_json_text)
        if pj_desc:
            return _cap(pj_desc)

    return f"Project: {fallback_repo_slug}"


def _readme_first_paragraph(readme: str) -> str | None:
    """Walk lines: skip frontmatter → HTML block → badges → headings → take first ≥40-char paragraph."""
    text = _strip_frontmatter(readme)
    lines = _strip_html_blocks(text.split("\n"))
    para_lines: list[str] = []

    for line in lines:
        if _BADGE_LINE_RE.match(line):
            continue
        if _ATX_HEADING_RE.match(line):
            if para_lines:
                candidate = " ".join(para_lines).strip()
                if len(candidate) >= _DESCRIPTION_MIN_CHARS:
                    return candidate
                para_lines = []
            continue
        if line.strip() == "":
            if para_lines:
                candidate = " ".join(para_lines).strip()
                if len(candidate) >= _DESCRIPTION_MIN_CHARS:
                    return candidate
                para_lines = []
            continue
        para_lines.append(line.strip())

    if para_lines:
        candidate = " ".join(para_lines).strip()
        if len(candidate) >= _DESCRIPTION_MIN_CHARS:
            return candidate
    return None


_HTML_CLOSE_RE = re.compile(r"</\w+\s*>")


def _strip_html_blocks(lines: list[str]) -> list[str]:
    """Remove HTML block regions. Pragmatic, not a full parser.

    A block starts on any line whose first non-whitespace char is ``<``
    AND the trimmed line does NOT contain a matching close tag on the
    same line (so single-line ``<br/>`` is preserved as-is and skipped
    only if it's badge-shaped). The block ends at the first line
    containing ``</tag>`` close-tag syntax.
    """
    out: list[str] = []
    in_block = False
    for line in lines:
        stripped = line.lstrip()
        if not in_block:
            if stripped.startswith("<") and not _looks_self_closing_inline(stripped):
                # Same-line open + close → keep whatever prose follows the
                # closing tag. Otherwise the whole line gets dropped, which
                # eats real description text from READMEs that lead with
                # ``<sub>note</sub> Some prose follows.``.
                close_match = _HTML_CLOSE_RE.search(stripped)
                if close_match:
                    tail = stripped[close_match.end():].strip()
                    if tail:
                        out.append(tail)
                    continue
                in_block = True
                continue
            out.append(line)
        else:
            if _HTML_CLOSE_RE.search(line):
                in_block = False
            # else: still inside, skip
    return out


def _looks_self_closing_inline(stripped: str) -> bool:
    """Heuristic for ``<br/>`` / ``<img …/>`` style on its own line.

    We only treat the line as an HTML *block* opener if it doesn't look
    self-closing — otherwise badge-row detection later would already
    catch it.
    """
    return stripped.endswith("/>") and "</" not in stripped


def _pyproject_description(text: str) -> str | None:
    """Parse ``[project] description = "..."`` (or single-quoted) from pyproject.toml.

    Pragmatic regex, not a full TOML parser — pyproject is the only
    file shape we read this way and we don't want to pull in tomllib
    just for one field. (Python 3.11+ has tomllib stdlib but importing
    is heavier than this regex; revisit if more TOML reads land.)
    """
    # Look for [project] section, then description = "value" within it.
    # We accept anything up to the next [section] header.
    section_match = re.search(
        r"\[project\](.*?)(?=^\[|\Z)", text, re.DOTALL | re.MULTILINE
    )
    if not section_match:
        return None
    section = section_match.group(1)
    desc_match = re.search(
        r"""^\s*description\s*=\s*['"]([^'"]+)['"]""",
        section,
        re.MULTILINE,
    )
    if desc_match:
        return desc_match.group(1).strip() or None
    return None


def _package_json_description(text: str) -> str | None:
    """Parse ``"description": "..."`` from a package.json string."""
    import json

    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    desc = data.get("description")
    if isinstance(desc, str) and desc.strip():
        return desc.strip()
    return None


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    return text[end + 4:].lstrip("\n")


def _cap(s: str) -> str:
    """Collapse whitespace, cap at ``_DESCRIPTION_MAX_CHARS`` with ellipsis."""
    s = " ".join(s.split())
    if len(s) <= _DESCRIPTION_MAX_CHARS:
        return s
    return s[: _DESCRIPTION_MAX_CHARS - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# Section extraction (Overview / Conventions / Architecture)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HarnessSections:
    """Output bundle from :func:`parse_harness_files`."""

    description: str
    overview: str
    conventions: str
    architecture: str


def parse_harness_files(
    *,
    readme: str | None = None,
    claude_md: str | None = None,
    agents_md: str | None = None,
    cursorrules: str | None = None,
    copilot_instructions: str | None = None,
    pyproject_text: str | None = None,
    package_json_text: str | None = None,
    fallback_repo_slug: str = "project",
) -> HarnessSections:
    """Compose the four canonical sections from whichever harness files were found.

    Each input is independent: pass ``None`` for any source not present
    in the repo. Returns a :class:`HarnessSections` with empty strings
    for unfilled sections (caller renders only non-empty ones).
    """
    description = extract_description(
        readme=readme,
        pyproject_text=pyproject_text,
        package_json_text=package_json_text,
        fallback_repo_slug=fallback_repo_slug,
    )
    overview = _overview_from_sources(readme=readme, pyproject_text=pyproject_text)
    conventions = _conventions_from_sources(
        claude_md=claude_md,
        agents_md=agents_md,
        cursorrules=cursorrules,
        copilot_instructions=copilot_instructions,
    )
    architecture = _architecture_from_sources(readme=readme, claude_md=claude_md)
    return HarnessSections(
        description=description,
        overview=overview,
        conventions=conventions,
        architecture=architecture,
    )


def _overview_from_sources(
    *, readme: str | None, pyproject_text: str | None
) -> str:
    """Take the README's prose intro (after badges/HTML/headings) up to ~3 paragraphs.

    Falls back to "(no overview available)" when no README is present.
    """
    if not readme:
        return ""
    paras = _readme_paragraphs(readme, max_paragraphs=3)
    if paras:
        return "\n\n".join(paras)
    return ""


def _readme_paragraphs(readme: str, *, max_paragraphs: int) -> list[str]:
    text = _strip_frontmatter(readme)
    lines = _strip_html_blocks(text.split("\n"))
    paras: list[str] = []
    current: list[str] = []

    def flush() -> None:
        nonlocal current
        if current:
            joined = " ".join(current).strip()
            if joined:
                paras.append(joined)
            current = []

    for line in lines:
        if _BADGE_LINE_RE.match(line):
            continue
        if _ATX_HEADING_RE.match(line):
            flush()
            if len(paras) >= max_paragraphs:
                break
            continue
        if line.strip() == "":
            flush()
            if len(paras) >= max_paragraphs:
                break
            continue
        current.append(line.strip())
    flush()
    return paras[:max_paragraphs]


def _conventions_from_sources(
    *,
    claude_md: str | None,
    agents_md: str | None,
    cursorrules: str | None,
    copilot_instructions: str | None,
) -> str:
    """Concatenate convention sources with attribution headings.

    Each source becomes a small subsection ("From CLAUDE.md", "From
    AGENTS.md", etc.) so the human reader can trace where a rule
    came from. Empty/missing sources are skipped.
    """
    pieces: list[str] = []
    for label, content in (
        ("CLAUDE.md", claude_md),
        ("AGENTS.md", agents_md),
        (".cursorrules", cursorrules),
        (".github/copilot-instructions.md", copilot_instructions),
    ):
        if not content:
            continue
        body = _strip_frontmatter(content).strip()
        if not body:
            continue
        # Truncate gigantic CLAUDE.md inputs to keep the project note scannable.
        if len(body) > 1500:
            body = body[:1500].rsplit("\n", 1)[0] + "\n\n_(truncated; full file in repo)_"
        pieces.append(f"### From `{label}`\n\n{body}")
    return "\n\n".join(pieces)


def _architecture_from_sources(
    *, readme: str | None, claude_md: str | None
) -> str:
    """Look for an "## Architecture" heading in either source; copy its body.

    Returns empty if no architecture section is found anywhere — the
    stub generator omits the entire ``## Architecture`` heading in
    that case.
    """
    for source in (claude_md, readme):
        if not source:
            continue
        section = _section_body(source, "architecture")
        if section:
            return section.strip()
    return ""


def _section_body(text: str, heading_word: str) -> str | None:
    """Return the body under any ATX heading whose first word matches ``heading_word``.

    Case-insensitive; matches `## Architecture` and `## Architecture Overview`
    alike, but not `## Architectural Decisions`.
    """
    body = _strip_frontmatter(text)
    lines = body.split("\n")
    capture_from = None
    capture_to = len(lines)
    for i, line in enumerate(lines):
        m = _ATX_HEADING_RE.match(line)
        if not m:
            continue
        # Tokenize the heading text (everything after the # marks).
        tokens = line[m.end():].lower().split()
        if not tokens:
            continue
        if capture_from is None and tokens[0] == heading_word.lower():
            capture_from = i + 1
        elif capture_from is not None:
            # Next heading at any level closes the section.
            capture_to = i
            break
    if capture_from is None:
        return None
    return "\n".join(lines[capture_from:capture_to])
