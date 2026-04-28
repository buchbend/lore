"""Parse ExitPlanMode markdown (or hand-edited plan text) → StructuredPlan.

Three step-detection modes (mutually exclusive, first match wins):

* ``headings`` — ≥2 sibling headings matching ``### Phase N`` /
  ``### Step N`` / ``### s<N>`` / ``## N. ``. Document order
  determines ``s1..sN``.
* ``list`` — ≥2 sibling ``^\\d+\\.`` items at column zero (top-level
  numbered list, *not* nested inside another list). Each item becomes
  one step.
* ``single`` — neither matched. The full body becomes one big step.
  Not an error; the plan still files with one anchor.

Fenced code blocks (``` ``` ``` `` ` ``) are stripped *before* scanning so
``1. foo`` inside a code example doesn't fire list mode.

Permissive payload-shape fallback: :func:`parse_payload` accepts a raw
JSON dict and tries documented field names in order before falling back
to "any string-typed value in tool_input ≥100 chars." Logs which path
matched via the returned ``payload_field``.
"""
from __future__ import annotations

import re
from typing import Any

from lore_core.session import slugify

from .types import PlanStep, StructuredPlan

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Field names tried in order by :func:`parse_payload`. The first non-empty
#: string wins. The fallback "any string ≥100 chars in tool_input" handles
#: future Claude Code renames without code changes.
_PAYLOAD_FIELDS = ("plan", "plan_text", "content", "text", "markdown")
_FALLBACK_MIN_CHARS = 100

#: Slug fallback length cap when no H1 heading is present.
_SLUG_FALLBACK_CHARS = 40

#: Step heading regexes — match the heading *line* exactly (no body capture).
#: Order matters: the union forms the trigger set for the ``headings`` mode.
_STEP_HEADING_PATTERNS = (
    re.compile(r"^###\s+(?:Phase|Step)\s+(\d+)\b", re.IGNORECASE),
    re.compile(r"^###\s+s(\d+)\b", re.IGNORECASE),
    re.compile(r"^##\s+(\d+)\.\s+", re.IGNORECASE),
)

#: Top-level numbered-list item: digits + period + space at column zero.
#: Indented list items (nested) are excluded so a single 1-2-3 sub-list
#: inside a paragraph doesn't trigger list mode.
_TOP_NUMBERED_LIST_RE = re.compile(r"^(\d+)\.\s+(.*)$")

#: Any ATX heading (used to split body_intro from the first step).
_ATX_HEADING_RE = re.compile(r"^#{1,6}\s+")

#: Markdown list-item marker at the start of a *stripped* line. Used to
#: distinguish prose continuations (reflowed into the step title) from
#: sub-list items (kept in the body). Requires whitespace after the marker
#: so italicized text (``*foo*``) does not match.
_LIST_MARKER_RE = re.compile(r"^([-*+]|\d+\.)\s")

#: Fenced code block — ``` or ~~~ openers; matched non-greedily.
#: Triple-backtick variants only; we don't support indented code blocks.
_FENCE_RE = re.compile(r"^(```|~~~)", re.MULTILINE)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_payload(payload: dict[str, Any]) -> tuple[str | None, str]:
    """Extract plan markdown from a Claude Code hook JSON payload.

    Returns ``(text, source_field)`` — ``text`` is None if nothing
    extractable was found; ``source_field`` names which path matched
    (for telemetry).

    Search order:

    1. Documented ``tool_input.<field>`` names (``plan``, ``plan_text``, …).
    2. Longest string ≥100 chars anywhere in ``tool_input`` (handles future
       schema renames).
    3. Same lookups against ``tool_response`` — Claude Code's actual hook
       payload puts the plan in ``tool_response.plan`` when the model
       calls ExitPlanMode without a ``plan`` argument (the harness loads
       it from the plan file). ``tool_input`` is empty in that case.

    The 100-char fallback threshold rejects short slugs / IDs that happen
    to live alongside the plan text.
    """
    for source_name in ("tool_input", "tool_response"):
        section = payload.get(source_name)
        if not isinstance(section, dict):
            continue

        for field_name in _PAYLOAD_FIELDS:
            value = section.get(field_name)
            if isinstance(value, str) and value.strip():
                return value, f"{source_name}.{field_name}"

        # Fallback: prefer the LONGEST string >= threshold. Dict insertion
        # order is determined by the JSON producer, not by us — picking
        # the longest is more robust against future schema additions where
        # an extra string field happens to be listed before the real plan.
        longest_key: str | None = None
        longest_val: str | None = None
        longest_len = -1
        for key, value in section.items():
            if not isinstance(value, str):
                continue
            if len(value) < _FALLBACK_MIN_CHARS:
                continue
            if len(value) > longest_len:
                longest_len = len(value)
                longest_key = key
                longest_val = value
        if longest_val is not None:
            return longest_val, f"{source_name}.{longest_key}[fallback]"

    return None, "no-match"


def parse(text: str, *, slug_override: str | None = None) -> StructuredPlan:
    """Parse plan markdown into a :class:`StructuredPlan`.

    ``slug_override`` lets callers force a slug (e.g., from a manual
    ``slug:`` frontmatter the user prefixed). Otherwise the slug is
    derived from the first H1 heading, or the first 40 characters of
    plain text if no H1 is present.

    Always succeeds — non-conforming markdown degrades to a single-step
    plan rather than raising. The caller (hook handler) inspects
    ``plan.mode`` for telemetry but does not branch on it.
    """
    title = _extract_title(text)
    slug = slug_override or _derive_slug(text, title)

    # Strip fenced code blocks before step scanning. Title extraction
    # ran on the raw text because H1 inside a code fence is malformed
    # markdown anyway and we'd rather grab whatever's there than nothing.
    scan_text = _strip_fenced_blocks(text)

    steps, mode, body_intro = _detect_steps(scan_text, raw_text=text)

    return StructuredPlan(
        slug=slug,
        title=title,
        body_intro=body_intro,
        steps=steps,
        mode=mode,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_title(text: str) -> str:
    """Return the first H1 heading text, or empty string if absent."""
    for line in text.splitlines():
        if line.startswith("# ") and not line.startswith("## "):
            return line[2:].strip()
    return ""


def _derive_slug(text: str, title: str) -> str:
    """Slug from title if present; else first 40 chars of plain text."""
    if title:
        slug = slugify(title)
        if slug:
            return slug
    # Fall back to first non-empty, non-heading line.
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        slug = slugify(line[:_SLUG_FALLBACK_CHARS])
        if slug:
            return slug
    return "unnamed-plan"


def _strip_fenced_blocks(text: str) -> str:
    """Remove fenced code blocks (``` and ~~~) from text.

    Replaces each block with blank lines so line numbers downstream
    remain meaningful and a fence-internal numbered list cannot be
    mistaken for a step.
    """
    lines = text.split("\n")
    out: list[str] = []
    in_fence = False
    for line in lines:
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            out.append("")  # placeholder so line count stays stable
            continue
        out.append("" if in_fence else line)
    return "\n".join(out)


def _detect_steps(
    scan_text: str, *, raw_text: str
) -> tuple[list[PlanStep], str, str]:
    """Return ``(steps, mode, body_intro)``.

    Implements the three-mode dispatch documented at the module level.
    ``raw_text`` is consulted for body_intro extraction so fenced
    blocks in the intro round-trip verbatim.
    """
    # Mode 1: headings. Need ≥2 sibling step-headings.
    heading_hits = _find_step_headings(scan_text)
    if len(heading_hits) >= 2:
        steps = _steps_from_headings(scan_text, heading_hits)
        body_intro = _body_intro_before_first_heading(raw_text, heading_hits[0][0])
        return steps, "headings", body_intro

    # Mode 2: top-level numbered list. Need ≥2 sibling items at column 0.
    list_hits = _find_top_numbered_list(scan_text)
    if len(list_hits) >= 2:
        steps = _steps_from_list(scan_text, list_hits)
        body_intro = _body_intro_before_first_list_item(raw_text, list_hits[0][0])
        return steps, "list", body_intro

    # Mode 3: single. Whole body is one step. body_intro is the title
    # line (if any) and we put everything else into step s1.
    title_line_count = 1 if raw_text.lstrip().startswith("# ") else 0
    body = "\n".join(raw_text.splitlines()[title_line_count:]).strip()
    if not body:
        return [], "single", raw_text.strip()
    return [PlanStep(id="s1", title="", body=body)], "single", ""


def _find_step_headings(text: str) -> list[tuple[int, str, str]]:
    """Return list of ``(line_index, heading_text, step_title)``.

    ``step_title`` is the human-readable title (everything after the
    matched prefix), used by :func:`_steps_from_headings`.
    """
    hits: list[tuple[int, str, str]] = []
    for i, line in enumerate(text.split("\n")):
        for pat in _STEP_HEADING_PATTERNS:
            m = pat.match(line)
            if m:
                # Strip the matched prefix to recover the title.
                title = line[m.end():].lstrip(":").lstrip().rstrip()
                hits.append((i, line, title))
                break
    return hits


def _steps_from_headings(
    text: str, hits: list[tuple[int, str, str]]
) -> list[PlanStep]:
    """Slice the body into one step per heading hit."""
    lines = text.split("\n")
    steps: list[PlanStep] = []
    for idx, (line_idx, _heading, title) in enumerate(hits):
        body_start = line_idx + 1
        body_end = hits[idx + 1][0] if idx + 1 < len(hits) else len(lines)
        body = "\n".join(lines[body_start:body_end]).strip()
        steps.append(PlanStep(id=f"s{idx + 1}", title=title, body=body))
    return steps


def _find_top_numbered_list(text: str) -> list[tuple[int, str, str]]:
    """Return list of ``(line_index, marker, item_first_line)`` for top-level numbered items."""
    hits: list[tuple[int, str, str]] = []
    for i, line in enumerate(text.split("\n")):
        # Reject indented continuations; we want column-zero matches only.
        if line and line[0] in " \t":
            continue
        m = _TOP_NUMBERED_LIST_RE.match(line)
        if m:
            hits.append((i, m.group(1), m.group(2)))
    return hits


def _steps_from_list(
    text: str, hits: list[tuple[int, str, str]]
) -> list[PlanStep]:
    """Slice the body into one step per numbered-list item.

    The title is the first line of the item plus any indented prose
    continuation lines that follow (reflowed onto one line, separated
    by spaces). A sub-list (line starting with ``-``, ``*``, ``+`` or
    ``N. `` after stripping leading whitespace) or a blank line ends the
    title block; everything from there until the next top-level item
    (or the next non-indented line) becomes the body.

    Reflow matters: source markdown often hard-wraps a single sentence
    across two indented lines. Without reflow, the step title would be
    truncated mid-sentence and the truncated tail orphaned into the body.
    """
    lines = text.split("\n")
    steps: list[PlanStep] = []
    for idx, (line_idx, _marker, first_line_body) in enumerate(hits):
        body_start = line_idx + 1
        # Outer body window: up to the next top-level item, or up to the
        # first non-indented non-blank line — whichever comes first.
        next_hit_line = hits[idx + 1][0] if idx + 1 < len(hits) else len(lines)
        body_end = next_hit_line
        for j in range(body_start, next_hit_line):
            line = lines[j]
            if line and not line[0].isspace():
                body_end = j
                break

        # Within the outer window, split off leading PROSE continuation
        # lines into the title. A blank line or a sub-list marker ends
        # the title block.
        title_extra: list[str] = []
        body_kept_start = body_start
        for j in range(body_start, body_end):
            stripped = lines[j].strip()
            if not stripped:
                body_kept_start = j + 1
                break
            if _LIST_MARKER_RE.match(stripped):
                break
            title_extra.append(stripped)
            body_kept_start = j + 1

        title = " ".join(
            [first_line_body.strip(), *title_extra]
        ).strip()
        body_block = "\n".join(lines[body_kept_start:body_end]).strip()
        steps.append(
            PlanStep(
                id=f"s{idx + 1}",
                title=title,
                body=body_block,
            )
        )
    return steps


def _body_intro_before_first_heading(text: str, first_heading_line: int) -> str:
    """Lines before the first step heading, with the H1 line skipped."""
    lines = text.split("\n")
    intro_lines = lines[:first_heading_line]
    # Skip the H1 if it leads.
    if intro_lines and intro_lines[0].startswith("# "):
        intro_lines = intro_lines[1:]
    return "\n".join(intro_lines).strip()


def _body_intro_before_first_list_item(text: str, first_item_line: int) -> str:
    """Lines before the first numbered-list item, with the H1 line skipped.

    Also drops a trailing ``## Steps`` (or similar header) line that
    commonly precedes the list — reduces noise in the rendered note
    intro without losing semantic content.
    """
    lines = text.split("\n")
    intro_lines = lines[:first_item_line]
    if intro_lines and intro_lines[0].startswith("# "):
        intro_lines = intro_lines[1:]
    # Drop a trailing pure-header line if it's the last non-blank line.
    while intro_lines and not intro_lines[-1].strip():
        intro_lines.pop()
    if intro_lines and _ATX_HEADING_RE.match(intro_lines[-1]) and len(
        intro_lines[-1].split()
    ) <= 4:
        intro_lines.pop()
    return "\n".join(intro_lines).strip()
