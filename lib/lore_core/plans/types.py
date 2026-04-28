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
    """One step in a plan. ``id`` is the stable anchor (``s1``..``sN``)."""

    id: str
    title: str
    body: str  # markdown body of the step (heading content, not including the heading line)


# StepDetectionMode is a string for trivial logging; not an Enum because the
# value flows directly into HookEventLogger.emit(mode=...) with no parsing.
StepDetectionMode = str  # one of: "headings" | "list" | "single"


@dataclass
class StructuredPlan:
    """Output of the parser; input to the writer.

    ``slug`` is the kebab-case identifier; ``title`` is the human-readable
    H1 (rendered in SessionStart); ``body_intro`` is the prose between the
    title and the first step (preserved verbatim); ``steps`` is the
    ordered list of parsed steps; ``mode`` reports which detection branch
    fired (logged for telemetry / debugging parser pathologies).
    """

    slug: str
    title: str
    body_intro: str
    steps: list[PlanStep] = field(default_factory=list)
    mode: StepDetectionMode = "single"

    def step_ids(self) -> list[str]:
        return [s.id for s in self.steps]
