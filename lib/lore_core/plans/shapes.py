"""Typed verdicts returned by the markdown shape classifier.

The classifier (:mod:`markdown_adapter`) tokenizes a plan's headings
and list items once, then returns one of these verdict shapes. The
ingest dispatcher (:mod:`ingest`) routes on the verdict type:

* :class:`ShapeATXSteps` — sibling ATX headings at one level (``### Step
  1``, ``### P1``, ``### step-1``, ``## 1.``, etc.). The flat case.
* :class:`ShapeHierarchical` — H2 containers + H3 leaves where both
  levels form monotone sequences. Leaves win as steps; container
  titles fold into ``PlanStep.group``.
* :class:`ShapeNumberedList` — top-level ``1. … 2. …`` numbered list
  (no headings).
* :class:`ShapeCheckboxList` — ``- [ ] step`` markdown task list.
* :class:`ShapeAmbiguous` — multiple plausible interpretations; caller
  should fail loud rather than guess.
* :class:`ShapeUnknown` — no recognizable step structure.

Shapes carry the **hits** (heading lines + parsed metadata) so the
parser doesn't have to re-tokenize. ``hits`` is a list of opaque
dicts; consumers know which keys to expect from the shape type.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ShapeATXSteps:
    """≥2 sibling ATX headings at the same level forming a step sequence.

    ``level`` is the H-level (2 or 3). ``prefix_kind`` names the prefix
    the classifier matched (``"phase"``, ``"step"``, ``"p"``, ``"s"``,
    ``"step-N"``, ``"numeric"``, ``""``). ``hits`` carries the per-step
    metadata: ``{"line_index", "title", "ordinal", "raw_line"}``.
    """

    level: int
    prefix_kind: str
    hits: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ShapeHierarchical:
    """H2 container headings + H3 leaf-step headings forming a hierarchy.

    Example: ``## Phase 1 — Foundation`` with ``### 1.1``, ``### 1.2`` …
    The leaves are the actionable steps; containers annotate.

    ``container_level`` and ``item_level`` are the H-levels. ``hits`` is
    the flat ordered list of leaves (one entry per actionable step) —
    each carries ``{"line_index", "title", "ordinal", "sub_ordinal",
    "group", "raw_line"}`` where ``group`` is the parent container's
    title (used to populate :attr:`lore_core.plans.types.PlanStep.group`).
    """

    container_level: int
    item_level: int
    hits: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ShapeNumberedList:
    """Top-level numbered list (``1. foo`` at column 0) with ≥2 items.

    ``hits`` carries ``{"line_index", "title", "ordinal", "raw_line"}``.
    """

    hits: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ShapeCheckboxList:
    """Markdown task list (``- [ ] foo``) with ≥2 items.

    ``hits`` carries ``{"line_index", "title", "checked", "raw_line"}``
    where ``checked`` is a bool reflecting ``[x]`` vs ``[ ]``.
    """

    hits: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ShapeAmbiguous:
    """Multiple plausible interpretations — fail loud rather than guess.

    ``reason`` is a short human-readable explanation (e.g. "two disjoint
    numbered lists with no `## Steps` header"). ``candidates`` lists
    the shape names the classifier considered viable so consumers can
    surface a useful diagnostic.
    """

    reason: str
    candidates: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ShapeUnknown:
    """No recognizable step structure detected.

    Triggers a hard error at the hook entry point (no plan filed). The
    ``reason`` field carries the diagnostic stamped into the hook log.
    """

    reason: str
    diagnosis: dict[str, Any] = field(default_factory=dict)


#: Type alias for any shape verdict. Useful for return-type hints.
Shape = (
    ShapeATXSteps
    | ShapeHierarchical
    | ShapeNumberedList
    | ShapeCheckboxList
    | ShapeAmbiguous
    | ShapeUnknown
)
