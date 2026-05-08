"""ADR-candidate concept — validate, render, parse, and emit schema.

Pure helpers — no synthesis-pipeline dependencies — so all candidate-
related logic is unit-testable without spinning up the Phase-2 flow.

Shape (per PRD #61, slice #63):

    ## ADR candidates

    _ADR = Architecture Decision Record. Proposals worth promoting later;
    most sessions have none._

    - **{choice}**
      - Why: {rationale}
      - Instead of: {alternative_rejected}
      - Evidence: {evidence}

``validate`` is the confabulation filter: it returns ``None`` whenever
any of the four required fields is missing or empty, so the LLM cannot
produce a half-formed candidate even if the JSONSchema passes it through.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "ADRCandidate",
    "validate",
    "tool_schema_property",
    "render_section",
    "parse_section",
]

_GLOSS = (
    "_ADR = Architecture Decision Record. Proposals worth promoting later; "
    "most sessions have none._"
)

_MAX_ITEMS = 5
_MAX_CHOICE = 200
_MAX_RATIONALE = 400
_MAX_EVIDENCE = 400
_MAX_ALT = 200


@dataclass(frozen=True)
class ADRCandidate:
    choice: str
    rationale: str
    evidence: str
    alternative_rejected: str


def validate(raw: dict) -> ADRCandidate | None:
    """Return an ``ADRCandidate`` if all four fields are present and non-empty.

    Returns ``None`` for any missing or empty field — the structural
    confabulation filter that keeps the section's signal high.
    """
    if not isinstance(raw, dict):
        return None
    choice = (raw.get("choice") or "").strip()
    rationale = (raw.get("rationale") or "").strip()
    evidence = (raw.get("evidence") or "").strip()
    alt = (raw.get("alternative_rejected") or "").strip()
    if not (choice and rationale and evidence and alt):
        return None
    return ADRCandidate(
        choice=choice,
        rationale=rationale,
        evidence=evidence,
        alternative_rejected=alt,
    )


def tool_schema_property() -> dict:
    """JSONSchema fragment for the ``adr_candidates`` array property.

    Consumed by ``synthesis._phase2_tool_schema`` to build the LLM tool
    schema for both work-shape and discussion-shape variants.
    """
    return {
        "type": "array",
        "maxItems": _MAX_ITEMS,
        "items": {
            "type": "object",
            "properties": {
                "choice": {"type": "string", "maxLength": _MAX_CHOICE},
                "rationale": {"type": "string", "maxLength": _MAX_RATIONALE},
                "evidence": {"type": "string", "maxLength": _MAX_EVIDENCE},
                "alternative_rejected": {"type": "string", "maxLength": _MAX_ALT},
            },
            "required": ["choice", "rationale", "evidence", "alternative_rejected"],
            "additionalProperties": False,
        },
    }


def render_section(candidates: list[ADRCandidate]) -> str:
    """Emit the full ``## ADR candidates`` block.

    Returns empty string when the list is empty — the empty case is the
    *expected* default; the caller (``render_body_sections``) omits the
    section entirely when given an empty list.
    """
    if not candidates:
        return ""
    lines: list[str] = [
        "## ADR candidates",
        "",
        _GLOSS,
    ]
    for c in candidates:
        lines.extend([
            "",
            f"- **{c.choice}**",
            f"  - Why: {c.rationale}",
            f"  - Instead of: {c.alternative_rejected}",
            f"  - Evidence: {c.evidence}",
        ])
    return "\n".join(lines)


def parse_section(body_lines: list[str]) -> list[ADRCandidate]:
    """Scan ``body_lines`` for the ``## ADR candidates`` section and parse.

    Lenient best-effort: malformed or incomplete entries are silently
    dropped so old or manually-edited notes don't crash the parser.

    Designed to round-trip with ``render_section``:
        parse_section(render_section(candidates).splitlines()) == candidates
    """
    in_section = False
    candidates: list[ADRCandidate] = []
    current: dict[str, str] = {}

    def _flush() -> None:
        if current:
            c = validate(current)
            if c is not None:
                candidates.append(c)
            current.clear()

    for raw_line in body_lines:
        line = raw_line.rstrip()

        if line.strip() in ("## ADR candidates", "## Decisions made"):
            in_section = True
            current.clear()
            continue

        if not in_section:
            continue

        # Stop at any subsequent heading
        if line.startswith("## ") or line.startswith("# "):
            _flush()
            break

        stripped = line.strip()
        if not stripped or stripped.startswith("_"):
            continue

        lstripped = line.lstrip()

        # New top-level candidate bullet: - **choice**
        if lstripped.startswith("- **"):
            _flush()
            inner = lstripped[4:].rstrip()
            if inner.endswith("**"):
                inner = inner[:-2]
            current["choice"] = inner.strip()
        elif current:
            if lstripped.startswith("- Why: "):
                current["rationale"] = lstripped[len("- Why: "):].strip()
            elif lstripped.startswith("- Instead of: "):
                current["alternative_rejected"] = lstripped[len("- Instead of: "):].strip()
            elif lstripped.startswith("- Evidence: "):
                current["evidence"] = lstripped[len("- Evidence: "):].strip()

    _flush()
    return candidates
