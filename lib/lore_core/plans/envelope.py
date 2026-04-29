"""``lore.plan.envelope/1`` — structured ingest schema for plan filing.

The envelope is the first-class path for tools that can emit JSON
(future Cursor / Aider / CLI / MCP-based agents). It bypasses markdown
shape detection entirely; producers construct the canonical IR
directly. No regex zoo, no fall-through, no silent single-mode plans.

Schema v1:

* Required keys: ``schema`` (= ``"lore.plan.envelope/1"``), ``title``,
  ``steps`` (list, ≥1)
* Optional keys: ``description``, ``body_intro``, ``slug``, ``repo``
* Per-step required: ``title``
* Per-step optional: ``id`` (canonicalized on read), ``body``, ``group``

Validation is intentionally hand-rolled — the schema is small enough
that pulling in ``jsonschema`` would be overkill, and a transparent
validator is easier to evolve toward v2 when we need it.
"""
from __future__ import annotations

from typing import Any

from lore_core.session import slugify

from . import canonical
from .types import PlanStep, StructuredPlan

#: Schema discriminator. Bumped to ``lore.plan.envelope/2`` if a future
#: version breaks compatibility.
PLAN_ENVELOPE_V1 = "lore.plan.envelope/1"


class EnvelopeError(ValueError):
    """Raised when an envelope dict fails schema validation.

    Inherits from :class:`ValueError` so existing
    ``except ValueError`` blocks at producer boundaries still catch it.
    """


def from_envelope(env: Any) -> StructuredPlan:
    """Validate and convert a v1 envelope dict to a :class:`StructuredPlan`.

    Raises :class:`EnvelopeError` on:

    * non-dict input
    * missing or wrong-version ``schema`` field
    * missing / non-string ``title``
    * missing / non-list / empty ``steps``
    * any step missing or with non-string ``title``

    Step IDs are accepted in either canonical (``step-<N>``) or legacy
    (``s<N>``) form and canonicalized on the way in. Steps without an
    explicit ``id`` are assigned ``step-1, step-2, …`` in document order.
    """
    if not isinstance(env, dict):
        raise EnvelopeError(
            f"envelope must be a dict, got {type(env).__name__}"
        )

    schema = env.get("schema")
    if schema != PLAN_ENVELOPE_V1:
        raise EnvelopeError(
            f"envelope schema must be {PLAN_ENVELOPE_V1!r}, got {schema!r}"
        )

    title = env.get("title")
    if not isinstance(title, str) or not title.strip():
        raise EnvelopeError("envelope.title is required and must be a non-empty string")

    raw_steps = env.get("steps")
    if not isinstance(raw_steps, list):
        raise EnvelopeError(
            f"envelope.steps must be a list, got {type(raw_steps).__name__}"
        )
    if not raw_steps:
        raise EnvelopeError("envelope.steps must contain at least one step")

    steps = _build_steps(raw_steps)

    slug_raw = env.get("slug")
    if isinstance(slug_raw, str) and slug_raw.strip():
        slug = slugify(slug_raw.strip())
    else:
        slug = slugify(title.strip()) or "unnamed-plan"

    body_intro = env.get("body_intro") or ""
    if not isinstance(body_intro, str):
        raise EnvelopeError(
            f"envelope.body_intro must be a string, got {type(body_intro).__name__}"
        )

    return StructuredPlan(
        slug=slug,
        title=title.strip(),
        body_intro=body_intro.strip(),
        steps=steps,
        mode="envelope",
        confidence="structured",
        warnings=[],
    )


def _build_steps(raw_steps: list[Any]) -> list[PlanStep]:
    """Validate and construct the ``steps`` list from envelope input.

    Each step must be a dict with a non-empty string ``title``. ``id``
    is canonicalized; missing IDs get ``step-<idx+1>``. ``body`` and
    ``group`` are optional and default to empty/None. The producer's
    ID order is preserved verbatim so explicit IDs survive round-trip.
    """
    steps: list[PlanStep] = []
    for idx, raw in enumerate(raw_steps):
        if not isinstance(raw, dict):
            raise EnvelopeError(
                f"envelope.steps[{idx}] must be a dict, got {type(raw).__name__}"
            )
        s_title = raw.get("title")
        if not isinstance(s_title, str) or not s_title.strip():
            raise EnvelopeError(
                f"envelope.steps[{idx}].title is required and must be a non-empty string"
            )

        sid_raw = raw.get("id")
        if sid_raw is None:
            sid = canonical.step_id_for(idx + 1)
        elif isinstance(sid_raw, str) and sid_raw.strip():
            sid = canonical.canonicalize_step_id(sid_raw.strip())
            if canonical.parse_step_id_ordinal(sid) is None:
                raise EnvelopeError(
                    f"envelope.steps[{idx}].id={sid_raw!r} does not match "
                    f"step-<N> or legacy s<N>"
                )
        else:
            raise EnvelopeError(
                f"envelope.steps[{idx}].id must be a string or omitted"
            )

        body_raw = raw.get("body") or ""
        if not isinstance(body_raw, str):
            raise EnvelopeError(
                f"envelope.steps[{idx}].body must be a string, got {type(body_raw).__name__}"
            )

        group_raw = raw.get("group")
        if group_raw is not None and not isinstance(group_raw, str):
            raise EnvelopeError(
                f"envelope.steps[{idx}].group must be a string or omitted"
            )

        steps.append(
            PlanStep(
                id=sid,
                title=s_title.strip(),
                body=body_raw,
                group=group_raw.strip() if isinstance(group_raw, str) else None,
            )
        )
    return steps
