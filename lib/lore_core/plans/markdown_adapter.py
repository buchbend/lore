"""Shape classifier + markdown-to-:class:`StructuredPlan` parser.

Replaces the closed regex union in the legacy parser. The pipeline is:

1. :func:`tokenize_headings` — walk the body once (fence-aware), parse
   each ATX heading into ``(level, prefix_token, ordinal, sub_ordinal,
   title)``. Normalizes prefix words (``Phase``, ``Step``, ``P``, ``S``,
   etc.) and ordinals (arabic, hierarchical ``1.1``).
2. :func:`classify` — run structural probes against the heading
   sequence; return a typed :class:`Shape` verdict.
3. :func:`parse_markdown` — given the verdict, slice the body into
   :class:`PlanStep` instances. For hierarchical shapes, fold container
   titles into ``PlanStep.group``.

The classifier asks one question — *do ≥2 sibling headings at the
same level form a monotone sequence under any interpretation?* —
which subsumes every regex in the legacy union plus the new
hierarchical case (``## Phase N`` + ``### N.M``).
"""
from __future__ import annotations

import re
from typing import Any

from . import canonical
from .shapes import (
    Shape,
    ShapeAmbiguous,
    ShapeATXSteps,
    ShapeCheckboxList,
    ShapeHierarchical,
    ShapeNumberedList,
    ShapeUnknown,
)
from .types import PlanStep

# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------


#: An ATX heading line: 1-6 ``#`` then a space then text.
_ATX_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")

#: Hierarchical ordinal at the start of a heading title: ``1.2``, ``2.3.4``.
_HIER_RE = re.compile(r"^(\d+(?:\.\d+)+)\b")

#: Bare arabic ordinal at the start: ``1`` or ``01`` (one segment only).
_BARE_RE = re.compile(r"^(\d+)\b")

#: Word-prefix step heading: ``Phase 1``, ``Step 2``, ``Stage 3``, etc.
_WORD_PREFIX_RE = re.compile(
    r"^(Phase|Step|Stage|Task|Milestone)\s+(\d+)\b",
    re.IGNORECASE,
)

#: Letter-prefix step heading: ``P1``, ``S2``. Distinct from the canonical
#: ``step-1`` (which has its own match below).
_LETTER_PREFIX_RE = re.compile(r"^([PS])(\d+)\b", re.IGNORECASE)

#: Canonical (``step-1``) and legacy (``s1``) anchor IDs at the start
#: of a heading title.
_ANCHOR_PREFIX_RE = re.compile(r"^(step-(\d+)|s(\d+))\b", re.IGNORECASE)

#: Top-level numbered-list item: ``1. foo`` at column 0.
_TOP_NUMBERED_LIST_RE = re.compile(r"^(\d+)\.\s+(.+)$")

#: Top-level task-list item: ``- [ ] foo`` or ``- [x] foo`` at column 0.
_CHECKBOX_RE = re.compile(r"^[-*+]\s+\[([ xX])\]\s+(.+)$")

#: Fence opener / closer.
_FENCE_RE = re.compile(r"^(```|~~~)")


def _strip_fenced_blocks(text: str) -> str:
    """Replace fenced-code-block contents with blank lines (line counts preserved)."""
    out: list[str] = []
    in_fence = False
    for line in text.split("\n"):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            out.append("")
            continue
        out.append("" if in_fence else line)
    return "\n".join(out)


def tokenize_headings(text: str) -> list[dict[str, Any]]:
    """Parse every ATX heading line into a structured token.

    Returns a list of dicts (in document order) with keys:
    ``line_index``, ``level``, ``raw_line``, ``raw_title``,
    ``prefix_kind``, ``ordinal``, ``sub_ordinal``, ``clean_title``.

    ``prefix_kind`` is one of: ``"phase"``, ``"step"``, ``"stage"``,
    ``"task"``, ``"milestone"``, ``"p"``, ``"s"``, ``"step-N"``,
    ``"hierarchical"``, ``"numeric"``, ``""`` (no recognizable prefix).
    ``ordinal`` is the leading integer; ``sub_ordinal`` is the
    second segment for hierarchical (``2`` for ``1.2``); both are None
    when no numeric ordinal is detectable.

    Fenced code blocks are stripped *before* heading detection, so a
    ``### Step 1`` inside a Python example doesn't fire.
    """
    scan_text = _strip_fenced_blocks(text)
    out: list[dict[str, Any]] = []
    for i, line in enumerate(scan_text.split("\n")):
        m = _ATX_RE.match(line)
        if m is None:
            continue
        level = len(m.group(1))
        raw_title = m.group(2).strip()
        token: dict[str, Any] = {
            "line_index": i,
            "level": level,
            "raw_line": line,
            "raw_title": raw_title,
            "prefix_kind": "",
            "ordinal": None,
            "sub_ordinal": None,
            "clean_title": raw_title,
        }
        _classify_prefix(token)
        out.append(token)
    return out


def _classify_prefix(token: dict[str, Any]) -> None:
    """Mutate ``token`` to fill ``prefix_kind``, ``ordinal``, ``sub_ordinal``,
    ``clean_title`` based on the leading text of the heading."""
    title = token["raw_title"]

    # 1. Anchor IDs (``step-1`` / ``s1``) — must run before the bare ``s``
    #    letter-prefix check to avoid mis-classifying ``s1`` as letter ``s``.
    m = _ANCHOR_PREFIX_RE.match(title)
    if m:
        canonical_or_legacy = m.group(1)
        ord_str = m.group(2) or m.group(3)
        token["prefix_kind"] = "step-N"
        token["ordinal"] = int(ord_str)
        token["clean_title"] = _strip_after(title, len(canonical_or_legacy))
        return

    # 2. Word prefixes (Phase/Step/Stage/Task/Milestone).
    m = _WORD_PREFIX_RE.match(title)
    if m:
        token["prefix_kind"] = m.group(1).lower()
        token["ordinal"] = int(m.group(2))
        token["clean_title"] = _strip_after(title, m.end())
        return

    # 3. Hierarchical ``1.2`` BEFORE bare-arabic so ``1.2`` doesn't get
    #    classified as bare numeric ordinal 1 with subordinate "2 alpha".
    m = _HIER_RE.match(title)
    if m:
        parts = m.group(1).split(".")
        token["prefix_kind"] = "hierarchical"
        token["ordinal"] = int(parts[0])
        token["sub_ordinal"] = int(parts[1])
        token["clean_title"] = _strip_after(title, m.end())
        return

    # 4. Letter prefix (``P1`` / ``S1``).
    m = _LETTER_PREFIX_RE.match(title)
    if m:
        token["prefix_kind"] = m.group(1).lower()
        token["ordinal"] = int(m.group(2))
        token["clean_title"] = _strip_after(title, m.end())
        return

    # 5. Bare arabic ordinal (``1. setup`` rendered without a word).
    m = _BARE_RE.match(title)
    if m:
        token["prefix_kind"] = "numeric"
        token["ordinal"] = int(m.group(1))
        token["clean_title"] = _strip_after(title, m.end())
        return

    # 6. No recognizable step prefix — ``prefix_kind`` stays "".


def _strip_after(title: str, idx: int) -> str:
    """Return ``title[idx:]`` with one leading separator group removed.

    Step headings often render as ``Step 1: foo`` or ``Phase 1 — foo``;
    after slicing off the prefix we want the bare title without the
    delimiter. Em-dash (``—``), en-dash, hyphen, colon, and full stop
    are all common.

    Strips one separator (or one contiguous run of separators) — does
    NOT loop and devour every leading punctuation character. A title
    like ``:reflect: do thing`` keeps its leading colon if the prefix
    portion ended without a separator; only delimiters between the
    matched prefix and the human-readable title are stripped.
    """
    rest = title[idx:].lstrip()
    # Strip ONE leading run of separator characters (then any whitespace).
    # Stops after the first non-separator non-space character so titles
    # like ``:foo:`` or ``— — recovery`` keep their semantic content.
    j = 0
    while j < len(rest) and rest[j] in ":—–-.":
        j += 1
    if j > 0:
        rest = rest[j:].lstrip()
    return rest


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


def classify(text: str) -> Shape:
    """Return the structural verdict for ``text``.

    Probes in priority order:

    1. **Hierarchical** — H2 containers (``## Phase N`` / ``## N`` / etc.)
       with H3 hierarchical leaves (``### N.M``). Leaves win as steps;
       containers fold into groups.
    2. **ATX steps** — ≥2 sibling H2 or H3 headings with monotone
       step-shaped prefixes. Deepest level present wins.
    3. **Numbered list** — top-level ``1. … 2. …`` with ≥2 items, no
       step headings competing.
    4. **Checkbox list** — ≥2 ``- [ ]`` task items.
    5. **Unknown** — none of the above.
    """
    if not text.strip():
        return ShapeUnknown(
            reason="empty_body",
            diagnosis={"chars": 0},
        )

    tokens = tokenize_headings(text)

    # Probe 1: hierarchical (must beat plain ATX since H3 headings exist
    # in both shapes; the difference is whether H2 containers have
    # monotone ordinals matching their H3 children's leading numbers).
    hier = _try_hierarchical(tokens)
    if hier is not None:
        return hier

    # Probe 2: flat ATX steps.
    atx = _try_atx_steps(tokens)
    if atx is not None:
        return atx

    # Probe 3: numbered list (no headings, just ``1. … 2. …``).
    scan_text = _strip_fenced_blocks(text)
    nlist = _try_numbered_list(scan_text)
    if nlist is not None:
        return nlist

    # Probe 4: checkbox list.
    cbox = _try_checkbox_list(scan_text)
    if cbox is not None:
        return cbox

    return ShapeUnknown(
        reason="no_recognized_structure",
        diagnosis={
            "headings_found": len(tokens),
            "step_shaped_headings": sum(1 for t in tokens if t["prefix_kind"]),
        },
    )


def _try_hierarchical(tokens: list[dict[str, Any]]) -> ShapeHierarchical | None:
    """Detect ``## Phase N`` containers + ``### N.M`` leaves.

    Required structure:
    * ≥1 H2 container with a step-shaped prefix (Phase/Step/numeric/word/letter)
    * ≥2 H3 leaves with hierarchical ``N.M`` ordinals
    * **Each leaf's major ordinal must match its enclosing container's ordinal.**
      An orphan leaf (e.g. ``### 99.42`` inside ``## Phase 1``) is dropped
      from the step list rather than promoted with the wrong ``group``
      annotation. If dropping orphans leaves fewer than 2 valid leaves,
      hierarchical detection fails and the classifier falls through to
      the next probe.

    Each accepted leaf is annotated with the parent H2 container's title.
    """
    h2 = [t for t in tokens if t["level"] == 2 and t["ordinal"] is not None]
    h3_hier = [
        t for t in tokens
        if t["level"] == 3 and t["prefix_kind"] == "hierarchical"
    ]
    if len(h2) < 1 or len(h3_hier) < 2:
        return None

    # Walk in document order, tracking the most recent step-shaped H2 as
    # the container. Validate each leaf's major ordinal against its
    # container before accepting it.
    leaves: list[dict[str, Any]] = []
    current_container: dict[str, Any] | None = None
    for t in tokens:
        if t["level"] == 2 and t["ordinal"] is not None:
            current_container = t
            continue
        if t["level"] == 3 and t["prefix_kind"] == "hierarchical":
            if current_container is None:
                # Leaf before any container — orphan; skip rather than
                # assign a fake group.
                continue
            if t["ordinal"] != current_container["ordinal"]:
                # Major ordinal disagrees with parent (e.g. ### 99.42
                # inside ## Phase 1). Drop the orphan; the user likely
                # meant a typo or hand-edited mid-flight.
                continue
            leaf = dict(t)  # copy so we can annotate
            leaf["group"] = _container_group_label(current_container)
            leaves.append(leaf)

    if len(leaves) < 2:
        return None

    return ShapeHierarchical(
        container_level=2,
        item_level=3,
        hits=leaves,
    )


def _container_group_label(container: dict[str, Any]) -> str:
    """Render the container's ``group`` annotation.

    Reconstructs the readable title — e.g. ``"Phase 1 — Foundation"``
    — from the container's raw title (which may have lost some
    formatting in tokenization). We use ``raw_title`` directly so the
    group preserves the user's exact wording.
    """
    return container["raw_title"]


def _try_atx_steps(tokens: list[dict[str, Any]]) -> ShapeATXSteps | None:
    """Detect ≥2 sibling ATX headings at the same level with step prefixes."""
    # Group tokens by level; within each level, look for runs with
    # consistent prefix_kind and monotone ordinals.
    if not tokens:
        return None

    # Prefer the deeper level when both H2 and H3 have step shapes — H3
    # is the conventional step granularity in plans we've seen.
    for level in (3, 2):
        candidates = [
            t for t in tokens
            if t["level"] == level and t["prefix_kind"] and t["ordinal"] is not None
        ]
        if len(candidates) < 2:
            continue
        # Filter to the dominant prefix_kind run (e.g. all "phase" or all "step-N").
        # In the failing-plan style every H3 is "hierarchical" — that's
        # already excluded from this fallback because hierarchical wins
        # at probe 1. So here we expect a homogeneous step run.
        kinds = {t["prefix_kind"] for t in candidates}
        if len(kinds) > 1:
            # Mixed step shapes at one level — pick the most common as
            # primary; if tied, fall through to ambiguous.
            from collections import Counter

            counter = Counter(t["prefix_kind"] for t in candidates)
            dominant, _ = counter.most_common(1)[0]
            candidates = [t for t in candidates if t["prefix_kind"] == dominant]
            if len(candidates) < 2:
                continue
            kinds = {dominant}
        prefix_kind = next(iter(kinds))
        return ShapeATXSteps(
            level=level,
            prefix_kind=prefix_kind,
            hits=candidates,
        )

    return None


#: Heading line that introduces a steps list (``## Steps``, ``### Plan steps``).
#: Used for disambiguation when a document has multiple numbered runs.
_STEPS_INTRO_HEADING_RE = re.compile(
    r"^#{1,6}\s+(?:plan\s+)?steps\s*$", re.IGNORECASE
)


def _try_numbered_list(scan_text: str) -> ShapeNumberedList | None:
    """Detect ≥2 top-level ``N. foo`` items at column 0 forming one coherent run.

    Disambiguation strategy:

    1. Group hits into contiguous runs separated by ATX headings.
    2. Discard runs of fewer than two items.
    3. If any run is preceded by a ``## Steps``-like heading, that
       run wins (explicit author intent).
    4. Otherwise, accept the run only when there is exactly one run.
       Multiple disjoint runs (Goals + Risks + Verification, etc.) are
       ambiguous; return None and let the classifier fall through.
    """
    lines = scan_text.split("\n")

    # Stage 1: collect raw top-level hits.
    raw_hits: list[dict[str, Any]] = []
    for i, line in enumerate(lines):
        if line and line[0].isspace():
            continue
        m = _TOP_NUMBERED_LIST_RE.match(line)
        if m:
            raw_hits.append({
                "line_index": i,
                "ordinal": int(m.group(1)),
                "title": m.group(2).strip(),
                "raw_line": line,
            })
    if len(raw_hits) < 2:
        return None

    # Stage 2: group into contiguous runs (split on intervening ATX headings).
    runs: list[list[dict[str, Any]]] = [[raw_hits[0]]]
    for prev, hit in zip(raw_hits, raw_hits[1:]):
        if _has_heading_between(lines, prev["line_index"], hit["line_index"]):
            runs.append([hit])
        else:
            runs[-1].append(hit)

    candidate_runs = [r for r in runs if len(r) >= 2]
    if not candidate_runs:
        return None

    # Stage 3: explicit ``## Steps``-prefixed run wins.
    for run in candidate_runs:
        if _preceded_by_steps_heading(lines, run[0]["line_index"]):
            hits = _reflow_titles(lines, run)
            return ShapeNumberedList(hits=hits)

    # Stage 4: single coherent run is unambiguous.
    if len(candidate_runs) == 1:
        hits = _reflow_titles(lines, candidate_runs[0])
        return ShapeNumberedList(hits=hits)

    # Multiple disjoint runs without a steps heading — ambiguous.
    return None


def _has_heading_between(lines: list[str], start: int, end: int) -> bool:
    """True if any ATX heading line falls strictly between ``start`` and ``end``."""
    for j in range(start + 1, end):
        if _ATX_RE.match(lines[j]):
            return True
    return False


def _preceded_by_steps_heading(lines: list[str], start: int) -> bool:
    """True if the nearest non-blank line above ``start`` is a steps-intro heading."""
    j = start - 1
    while j >= 0 and not lines[j].strip():
        j -= 1
    if j < 0:
        return False
    return bool(_STEPS_INTRO_HEADING_RE.match(lines[j].strip()))


def _reflow_titles(
    lines: list[str], run: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Mutate ``run`` in place to reflow hard-wrapped continuation lines into titles.

    Claude Code plans often hard-wrap a long sentence across two indented
    lines under a numbered item. Without reflow the title is truncated
    mid-sentence and the tail orphans into the body. A blank line or a
    sub-list marker ends the title block.
    """
    for idx, hit in enumerate(run):
        body_start = hit["line_index"] + 1
        next_line = (
            run[idx + 1]["line_index"] if idx + 1 < len(run) else len(lines)
        )
        # Outer body window: up to next top-level item (run boundary) OR
        # next non-indented non-blank line — whichever comes first.
        for j in range(body_start, next_line):
            line = lines[j]
            if line and not line[0].isspace():
                next_line = j
                break

        # Within outer window, leading prose (no marker, non-blank) joins title.
        title_extra: list[str] = []
        body_kept_start = body_start
        for j in range(body_start, next_line):
            stripped = lines[j].strip()
            if not stripped:
                body_kept_start = j + 1
                break
            if _LIST_MARKER_RE.match(stripped):
                break
            title_extra.append(stripped)
            body_kept_start = j + 1
        if title_extra:
            hit["title"] = " ".join([hit["title"], *title_extra])
        # Record where body actually starts so the slicer skips title lines.
        hit["body_start_override"] = body_kept_start
        hit["body_end_override"] = next_line
    return run


_LIST_MARKER_RE = re.compile(r"^([-*+]|\d+\.)\s")


def _try_checkbox_list(scan_text: str) -> ShapeCheckboxList | None:
    hits: list[dict[str, Any]] = []
    for i, line in enumerate(scan_text.split("\n")):
        if line and line[0].isspace():
            continue
        m = _CHECKBOX_RE.match(line)
        if m:
            hits.append({
                "line_index": i,
                "checked": m.group(1).lower() == "x",
                "title": m.group(2).strip(),
                "raw_line": line,
            })
    if len(hits) < 2:
        return None
    return ShapeCheckboxList(hits=hits)


# ---------------------------------------------------------------------------
# Body extractor
# ---------------------------------------------------------------------------


def parse_markdown(text: str, shape: Shape) -> tuple[list[PlanStep], str]:
    """Slice the markdown body into :class:`PlanStep` instances per ``shape``.

    Returns ``(steps, body_intro)``. ``body_intro`` is the prose
    between the title (H1, if any) and the first step heading or list
    item — *non-step* H2 sections (Context, Goals, etc.) remain in
    body_intro since they're framing content, not steps.

    Routing:
    * :class:`ShapeATXSteps` — slice between hits at ``shape.level``.
    * :class:`ShapeHierarchical` — flatten leaves, set ``group`` from
      hits.
    * :class:`ShapeNumberedList` — slice between list items.
    * :class:`ShapeCheckboxList` — one step per checkbox line.
    * :class:`ShapeUnknown` / :class:`ShapeAmbiguous` — return ``([], "")``;
      the dispatcher decides the policy (typically: hard-fail at hook).
    """
    if isinstance(shape, ShapeUnknown):
        return [], ""
    if isinstance(shape, ShapeAmbiguous):
        return [], ""

    raw_lines = text.split("\n")
    first_step_line = _first_step_line(shape)
    body_intro = _extract_body_intro(raw_lines, first_step_line)

    if isinstance(shape, ShapeATXSteps):
        return _steps_from_atx(raw_lines, shape), body_intro
    if isinstance(shape, ShapeHierarchical):
        return _steps_from_hierarchical(raw_lines, shape), body_intro
    if isinstance(shape, ShapeNumberedList):
        return _steps_from_numbered_list(raw_lines, shape), body_intro
    if isinstance(shape, ShapeCheckboxList):
        return _steps_from_checkbox_list(raw_lines, shape), body_intro

    return [], body_intro  # exhaustive — silence mypy


def _first_step_line(shape: Shape) -> int | None:
    """Line number of the first step in the shape, or None if shape has no hits."""
    hits = getattr(shape, "hits", None)
    if not hits:
        return None
    return hits[0]["line_index"]


def _extract_body_intro(raw_lines: list[str], first_step_line: int | None) -> str:
    """Return the text between the H1 title (if any) and the first step.

    Non-step H2 sections (Context, Goals, Risks, etc.) BEFORE the
    first step are preserved as framing content; this matters for
    plans where Phase headings come after a Goals section.
    """
    # Skip leading H1 if present.
    if raw_lines and raw_lines[0].startswith("# ") and not raw_lines[0].startswith("## "):
        intro_start = 1
    else:
        intro_start = 0

    end = first_step_line if first_step_line is not None else len(raw_lines)
    intro = "\n".join(raw_lines[intro_start:end]).strip()
    return intro


def _steps_from_atx(
    raw_lines: list[str], shape: ShapeATXSteps
) -> list[PlanStep]:
    steps: list[PlanStep] = []
    hits = shape.hits
    for idx, hit in enumerate(hits):
        body_start = hit["line_index"] + 1
        body_end = (
            hits[idx + 1]["line_index"] if idx + 1 < len(hits) else len(raw_lines)
        )
        body = "\n".join(raw_lines[body_start:body_end]).strip()
        steps.append(
            PlanStep(
                id=canonical.step_id_for(idx + 1),
                title=hit["clean_title"] or hit["raw_title"],
                body=body,
            )
        )
    return steps


def _steps_from_hierarchical(
    raw_lines: list[str], shape: ShapeHierarchical
) -> list[PlanStep]:
    steps: list[PlanStep] = []
    hits = shape.hits
    for idx, hit in enumerate(hits):
        body_start = hit["line_index"] + 1
        # Body extends to the next leaf — but may cross H2 boundaries.
        # That's fine; users who put prose between phases want it
        # attached to the preceding step.
        body_end = (
            hits[idx + 1]["line_index"] if idx + 1 < len(hits) else len(raw_lines)
        )
        body = "\n".join(raw_lines[body_start:body_end]).strip()
        steps.append(
            PlanStep(
                id=canonical.step_id_for(idx + 1),
                title=hit["clean_title"] or hit["raw_title"],
                body=body,
                group=hit.get("group"),
            )
        )
    return steps


def _steps_from_numbered_list(
    raw_lines: list[str], shape: ShapeNumberedList
) -> list[PlanStep]:
    """Slice top-level numbered items into steps.

    When reflow extended a title across continuation lines, those
    lines were already absorbed into the title — body slicing uses
    ``body_start_override`` / ``body_end_override`` set by
    :func:`_reflow_titles` so body content doesn't duplicate the
    title.
    """
    steps: list[PlanStep] = []
    hits = shape.hits
    for idx, hit in enumerate(hits):
        body_start = hit.get("body_start_override", hit["line_index"] + 1)
        body_end = hit.get(
            "body_end_override",
            hits[idx + 1]["line_index"] if idx + 1 < len(hits) else len(raw_lines),
        )
        body = "\n".join(raw_lines[body_start:body_end]).strip()
        steps.append(
            PlanStep(
                id=canonical.step_id_for(idx + 1),
                title=hit["title"],
                body=body,
            )
        )
    return steps


def _steps_from_checkbox_list(
    raw_lines: list[str], shape: ShapeCheckboxList
) -> list[PlanStep]:
    """One step per checkbox item; body is the indented continuation (if any)."""
    steps: list[PlanStep] = []
    hits = shape.hits
    for idx, hit in enumerate(hits):
        body_start = hit["line_index"] + 1
        next_hit_line = (
            hits[idx + 1]["line_index"] if idx + 1 < len(hits) else len(raw_lines)
        )
        # Body is indented continuation up to next checkbox or non-indented line.
        body_end = next_hit_line
        for j in range(body_start, next_hit_line):
            line = raw_lines[j]
            if line and not line[0].isspace():
                body_end = j
                break
        body = "\n".join(raw_lines[body_start:body_end]).strip()
        steps.append(
            PlanStep(
                id=canonical.step_id_for(idx + 1),
                title=hit["title"],
                body=body,
            )
        )
    return steps
