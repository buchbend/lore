"""Producer-facing entry point for plan ingestion.

This module is the **only** public path producers use to file a plan.
It accepts an :class:`IngestSource` (envelope JSON, hook payload, or
raw markdown), routes through the appropriate adapter or classifier,
and returns an :class:`IngestResult` containing a canonical
:class:`StructuredPlan` plus structured warnings.

The architecture splits "what producer is this?" from "what shape did
they emit?":

* **Envelope path** (``kind="envelope"``) — structured JSON that
  validates against ``lore.plan.envelope/1``. No shape detection;
  agents that can emit JSON skip parsing entirely. Confidence:
  ``structured``.
* **Hook payload path** (``kind="hook_payload"``) — Claude Code's
  ExitPlanMode JSON. The producer-keyed adapter (default:
  ``claude_code``) extracts the markdown blob; classification then
  runs.
* **Markdown path** (``kind="markdown"``) — opaque markdown directly.
  The shape classifier runs immediately.

Failure handling: producers that emit unstructured input get
``confidence="fallback"`` + structured warnings. The hook handler
treats ``fallback`` as a hard error (no plan filed). CLI tools may
choose to file with warnings stamped; that policy is outside this
module.

Phases 2 (envelope) and 3 (markdown classifier) flesh out the
implementations; this module ships the public dataclasses + dispatcher
shape so consumers can target a stable surface.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .types import StructuredPlan

# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


SourceKind = Literal["envelope", "markdown", "hook_payload"]
Confidence = Literal["structured", "high", "medium", "low", "fallback"]


@dataclass(frozen=True)
class IngestSource:
    """One ingestion input. Producers construct this; the dispatcher routes it.

    ``producer`` names the upstream tool (``"claude-code"``, ``"cursor"``,
    ``"cli"``, ``"mcp"``, ``"gha"``, …). It is REQUIRED and explicit —
    auto-detection would re-introduce silent misclassification, the very
    bug this redesign fixes.

    ``payload`` is the raw input. For ``kind="envelope"`` it's a dict;
    for ``kind="markdown"`` it's a string; for ``kind="hook_payload"``
    it's the Claude Code hook JSON dict.

    ``hint`` is optional caller-side metadata (``slug_override``,
    ``repo``, etc.) the dispatcher passes through to the writer.
    """

    kind: SourceKind
    payload: dict | str
    producer: str
    hint: dict | None = None


@dataclass(frozen=True)
class IngestWarning:
    """One structured ingestion diagnostic.

    ``code`` is a short stable identifier (``shape_unknown``,
    ``shape_ambiguous``, ``envelope_missing_field``, …) — used by
    consumers (lint, hook handler, MCP output) to render or branch.
    ``message`` is human-readable. ``detail`` is optional structured
    context (offending line, candidate shapes, missing field name).
    """

    code: str
    message: str
    detail: dict | None = None

    def to_dict(self) -> dict:
        d: dict = {"code": self.code, "message": self.message}
        if self.detail is not None:
            d["detail"] = self.detail
        return d


@dataclass(frozen=True)
class IngestResult:
    """Outcome of one :func:`ingest_plan` call.

    ``plan`` is the (canonical) :class:`StructuredPlan` ready for the
    writer. When ``confidence == "fallback"`` the plan is still
    populated (typically a single-step degraded form) so callers that
    choose to file it have something to write — but the hook handler
    is expected to refuse.

    ``adapter_name`` records which ingestion path served the source
    (envelope schema name, claude_code adapter, markdown classifier,
    etc.) — for telemetry and debugging.
    """

    plan: StructuredPlan
    warnings: list[IngestWarning] = field(default_factory=list)
    confidence: Confidence = "high"
    adapter_name: str = "unknown"

    def warnings_as_dicts(self) -> list[dict]:
        """Frontmatter-friendly serialization."""
        return [w.to_dict() for w in self.warnings]


# ---------------------------------------------------------------------------
# Dispatcher (shape only; full implementation lands with steps 6, 9-11)
# ---------------------------------------------------------------------------


def ingest_plan(source: IngestSource) -> IngestResult:
    """Route ``source`` through the right ingestion path; return the result.

    Routing:
      * ``kind="envelope"`` → :mod:`envelope` validator.
      * ``kind="hook_payload"`` → producer-keyed adapter (default:
        :mod:`adapters.claude_code`) → markdown classifier.
      * ``kind="markdown"`` → :mod:`markdown_adapter` shape classifier.

    Failure modes:
      * Envelope schema violations → :class:`EnvelopeError` propagates.
      * Markdown that the classifier returns :class:`ShapeUnknown` /
        :class:`ShapeAmbiguous` for → ``IngestResult`` with
        ``confidence="fallback"`` and structured warnings. The hook
        handler treats this as a hard error; CLI tools may stamp the
        warnings into frontmatter and proceed.
    """
    if source.kind == "envelope":
        from . import envelope

        payload = source.payload if isinstance(source.payload, dict) else {}
        plan = envelope.from_envelope(payload)
        return IngestResult(
            plan=plan,
            warnings=[],
            confidence="structured",
            adapter_name="envelope/v1",
        )

    if source.kind == "hook_payload":
        if not isinstance(source.payload, dict):
            raise ValueError(
                f"hook_payload source.payload must be a dict, "
                f"got {type(source.payload).__name__}"
            )
        from . import adapters

        adapter, is_known_producer = adapters.dispatch(source.producer)
        producer_warnings: list[IngestWarning] = []
        if not is_known_producer:
            producer_warnings.append(
                IngestWarning(
                    code="unknown_producer",
                    message=(
                        f"no adapter registered for producer "
                        f"{source.producer!r}; falling back to "
                        f"claude-code adapter"
                    ),
                )
            )

        markdown, source_field = adapter.extract(source.payload)
        if markdown is None:
            warning = IngestWarning(
                code="payload_no_plan",
                message=(
                    f"adapter {source.producer!r} found no plan markdown in "
                    f"hook payload (matched: {source_field})"
                ),
            )
            from .types import StructuredPlan

            warnings = [warning, *producer_warnings]
            plan = StructuredPlan(
                slug="unnamed-plan",
                title="",
                body_intro="",
                steps=[],
                mode="single",
                confidence="fallback",
                warnings=[w.to_dict() for w in warnings],
            )
            return IngestResult(
                plan=plan,
                warnings=warnings,
                confidence="fallback",
                adapter_name=f"hook/{source.producer}",
            )
        result = _ingest_markdown(markdown, producer=source.producer)
        # Preserve the hook adapter telemetry on top of the markdown route.
        return IngestResult(
            plan=result.plan,
            warnings=[*result.warnings, *producer_warnings],
            confidence=result.confidence,
            adapter_name=f"hook/{source.producer}:{source_field}",
        )

    if source.kind == "markdown":
        if not isinstance(source.payload, str):
            raise ValueError(
                f"markdown source.payload must be a string, got {type(source.payload).__name__}"
            )
        return _ingest_markdown(source.payload, producer=source.producer)

    raise ValueError(f"unknown IngestSource.kind={source.kind!r}")


def _ingest_markdown(text: str, *, producer: str) -> IngestResult:
    """Classify markdown and slice into a :class:`StructuredPlan`.

    Returns ``confidence="fallback"`` with structured warnings if the
    classifier can't recognize a step structure — never silently
    drops a malformed plan into single mode.
    """
    from lore_core.session import slugify

    from . import canonical, markdown_adapter
    from .shapes import ShapeAmbiguous, ShapeHierarchical, ShapeUnknown
    from .types import StructuredPlan

    shape = markdown_adapter.classify(text)
    title = _extract_h1(text)
    slug = slugify(title) if title else _fallback_slug(text)

    # Fallback plans preserve the original body in ``body_intro`` so
    # consumers that choose to file (CLI / import) keep the source
    # content visible. The hook handler treats fallback as a hard
    # error and refuses to file regardless.
    fallback_intro = _body_after_h1(text)

    if isinstance(shape, ShapeUnknown):
        warning = IngestWarning(
            code="shape_unknown",
            message=shape.reason,
            detail=shape.diagnosis,
        )
        plan = StructuredPlan(
            slug=slug,
            title=title,
            body_intro=fallback_intro,
            steps=[],
            mode="single",
            confidence="fallback",
            warnings=[warning.to_dict()],
        )
        return IngestResult(
            plan=plan,
            warnings=[warning],
            confidence="fallback",
            adapter_name="markdown/unknown",
        )

    if isinstance(shape, ShapeAmbiguous):
        warning = IngestWarning(
            code="shape_ambiguous",
            message=shape.reason,
            detail={"candidates": shape.candidates},
        )
        plan = StructuredPlan(
            slug=slug,
            title=title,
            body_intro=fallback_intro,
            steps=[],
            mode="single",
            confidence="fallback",
            warnings=[warning.to_dict()],
        )
        return IngestResult(
            plan=plan,
            warnings=[warning],
            confidence="fallback",
            adapter_name="markdown/ambiguous",
        )

    steps, body_intro = markdown_adapter.parse_markdown(text, shape)
    if not steps:
        # Defensive: classifier returned a recognized shape but yielded
        # no steps. Treat as fallback rather than silent zero-step plan.
        warning = IngestWarning(
            code="empty_steps",
            message=f"shape {type(shape).__name__} produced zero steps",
        )
        plan = StructuredPlan(
            slug=slug,
            title=title,
            body_intro="",
            steps=[],
            mode="single",
            confidence="fallback",
            warnings=[warning.to_dict()],
        )
        return IngestResult(
            plan=plan,
            warnings=[warning],
            confidence="fallback",
            adapter_name=f"markdown/{type(shape).__name__.removeprefix('Shape').lower()}",
        )

    mode = _shape_to_mode(shape)
    plan = StructuredPlan(
        slug=slug,
        title=title,
        body_intro=body_intro,
        steps=steps,
        mode=mode,
        confidence="high",
        warnings=[],
    )
    adapter_name = f"markdown/{type(shape).__name__.removeprefix('Shape').lower()}"
    return IngestResult(
        plan=plan,
        warnings=[],
        confidence="high",
        adapter_name=adapter_name,
    )


def _extract_h1(text: str) -> str:
    for line in text.split("\n"):
        if line.startswith("# ") and not line.startswith("## "):
            return line[2:].strip()
    return ""


def _body_after_h1(text: str) -> str:
    """Return the body with the H1 line stripped — used as fallback body_intro.

    Preserves the original markdown so a fallback-confidence plan
    still surfaces the user's content for inspection / re-authoring.
    """
    lines = text.split("\n")
    if lines and lines[0].startswith("# ") and not lines[0].startswith("## "):
        lines = lines[1:]
    return "\n".join(lines).strip()


def _fallback_slug(text: str) -> str:
    """Slug from first non-empty, non-heading line; or ``unnamed-plan``."""
    from lore_core.session import slugify

    for line in text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        slug = slugify(line[:40])
        if slug:
            return slug
    return "unnamed-plan"


def _shape_to_mode(shape: Any) -> str:
    """Map a Shape* type to the legacy ``mode`` string for telemetry."""
    name = type(shape).__name__.removeprefix("Shape").lower()
    return {
        "atxsteps": "headings",
        "hierarchical": "hierarchical",
        "numberedlist": "list",
        "checkboxlist": "checkbox",
    }.get(name, "single")
