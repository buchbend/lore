"""Plan dataclasses + StepStatus enum — the shape parser/writer/registry agree on."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class StepStatus(str, Enum):
    """Per-step status. ``pending`` is implicit (absence in step_status dict)."""

    DONE = "done"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"

    @classmethod
    def from_str(cls, value: str) -> "StepStatus":
        """Parse a string; raise ``ValueError`` on unknown values.

        ``pending`` is rejected because it should not appear in the dict —
        callers wanting to clear a status should remove the key, not set
        it to ``pending``.
        """
        try:
            return cls(value)
        except ValueError as e:
            raise ValueError(
                f"unknown step status {value!r}; must be one of {[s.value for s in cls]}"
            ) from e


@dataclass(frozen=True)
class PlanStep:
    """One step in a plan. ``id`` is the stable anchor (``step-1``..``step-N``).

    Plans created before the rename used ``s1``..``sN``; legacy IDs are
    accepted on read and canonicalized on the next re-capture (or via
    ``lore plan migrate-ids``). See :mod:`canonical`.

    ``group`` (optional) annotates the step with its parent container
    when the source plan used hierarchical headings — e.g. ``## Phase 1
    — Foundation`` containing ``### 1.1``, ``### 1.2`` lifts those
    leaves into flat ``step-1``, ``step-2`` IDs and stamps each with
    ``group="Phase 1 — Foundation"``. Pure metadata; the canonical IR
    stays flat.

    ``files`` is the authoritative list of file paths this step is
    expected to touch — written by the plan-authoring LLM as a
    ``Files:`` line in the step body and parsed via
    :func:`canonical.extract_step_files`. Used by Stop-hook commit
    attribution and PostToolUse:Edit pending→in_progress flips.
    """

    id: str
    title: str
    body: str  # markdown body of the step (heading content, not including the heading line)
    group: str | None = None
    files: list[str] = field(default_factory=list)


# StepDetectionMode is a string for trivial logging; not an Enum because the
# value flows directly into HookEventLogger.emit(mode=...) with no parsing.
StepDetectionMode = str  # one of: "headings" | "list" | "single" | "envelope" | "hierarchical"


@dataclass
class StructuredPlan:
    """Output of the parser; input to the writer.

    ``slug`` is the kebab-case identifier; ``title`` is the human-readable
    H1 (rendered in SessionStart); ``body_intro`` is the prose between the
    title and the first step (preserved verbatim); ``steps`` is the
    ordered list of parsed steps; ``mode`` reports which detection branch
    fired (logged for telemetry / debugging parser pathologies).

    ``confidence`` is the qualitative ingestion confidence — ``high`` for
    structured input (envelope) or unambiguous heading detection; ``low``
    or ``fallback`` when classification was uncertain. The hook handler
    uses this to decide whether to file the plan, surface a warning, or
    hard-fail the ingestion.

    ``warnings`` is the list of structured ingestion diagnostics
    (mismatched shape, ambiguous step boundaries, etc.). Stamped into
    the plan's ``parse_warnings`` frontmatter so the user sees them in
    ``lore_plan_active`` and SessionStart.
    """

    slug: str
    title: str
    body_intro: str
    steps: list[PlanStep] = field(default_factory=list)
    mode: StepDetectionMode = "single"
    confidence: str = "high"
    warnings: list[dict] = field(default_factory=list)

    def step_ids(self) -> list[str]:
        return [s.id for s in self.steps]
