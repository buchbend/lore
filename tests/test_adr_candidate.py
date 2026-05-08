"""Tests for lore_curator.adr_candidate — validate / render / parse / schema."""
from __future__ import annotations

from lore_curator import adr_candidate
from lore_curator.adr_candidate import ADRCandidate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _candidate(**kwargs) -> dict:
    base = {
        "choice": "use lede + bullets for Summary",
        "rationale": "Prose paragraphs are not scannable at a glance",
        "evidence": "user confirmed during grilling at turn 12",
        "alternative_rejected": "keep the 4-5 sentence prose block",
    }
    base.update(kwargs)
    return base


def _valid() -> ADRCandidate:
    return ADRCandidate(
        choice="use lede + bullets for Summary",
        rationale="Prose paragraphs are not scannable at a glance",
        evidence="user confirmed during grilling at turn 12",
        alternative_rejected="keep the 4-5 sentence prose block",
    )


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def test_validate_returns_candidate_when_all_fields_present():
    c = adr_candidate.validate(_candidate())
    assert c is not None
    assert c.choice == "use lede + bullets for Summary"
    assert c.rationale == "Prose paragraphs are not scannable at a glance"
    assert c.evidence == "user confirmed during grilling at turn 12"
    assert c.alternative_rejected == "keep the 4-5 sentence prose block"


def test_validate_returns_none_when_choice_missing():
    assert adr_candidate.validate(_candidate(choice="")) is None
    assert adr_candidate.validate({k: v for k, v in _candidate().items() if k != "choice"}) is None


def test_validate_returns_none_when_rationale_missing():
    assert adr_candidate.validate(_candidate(rationale="")) is None


def test_validate_returns_none_when_evidence_missing():
    assert adr_candidate.validate(_candidate(evidence="")) is None


def test_validate_returns_none_when_alternative_rejected_missing():
    assert adr_candidate.validate(_candidate(alternative_rejected="")) is None


def test_validate_strips_whitespace_from_fields():
    c = adr_candidate.validate(_candidate(choice="  choice  ", rationale="  r  ", evidence="  e  ", alternative_rejected="  a  "))
    assert c is not None
    assert c.choice == "choice"
    assert c.rationale == "r"


def test_validate_returns_none_for_non_dict():
    assert adr_candidate.validate("not a dict") is None  # type: ignore[arg-type]
    assert adr_candidate.validate(None) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# render_section
# ---------------------------------------------------------------------------


def test_render_section_empty_returns_empty_string():
    assert adr_candidate.render_section([]) == ""


def test_render_section_contains_h2_heading():
    out = adr_candidate.render_section([_valid()])
    assert "## ADR candidates" in out


def test_render_section_contains_gloss_line():
    out = adr_candidate.render_section([_valid()])
    assert "Architecture Decision Record" in out


def test_render_section_renders_choice_as_bold_bullet():
    out = adr_candidate.render_section([_valid()])
    assert "- **use lede + bullets for Summary**" in out


def test_render_section_renders_sub_bullets():
    out = adr_candidate.render_section([_valid()])
    assert "  - Why: Prose paragraphs are not scannable at a glance" in out
    assert "  - Instead of: keep the 4-5 sentence prose block" in out
    assert "  - Evidence: user confirmed during grilling at turn 12" in out


def test_render_section_multiple_candidates():
    c1 = ADRCandidate(choice="choice A", rationale="r A", evidence="e A", alternative_rejected="alt A")
    c2 = ADRCandidate(choice="choice B", rationale="r B", evidence="e B", alternative_rejected="alt B")
    out = adr_candidate.render_section([c1, c2])
    assert "- **choice A**" in out
    assert "- **choice B**" in out


# ---------------------------------------------------------------------------
# parse_section
# ---------------------------------------------------------------------------


def test_parse_section_round_trip_single_candidate():
    candidates = [_valid()]
    rendered = adr_candidate.render_section(candidates)
    parsed = adr_candidate.parse_section(rendered.splitlines())
    assert parsed == candidates


def test_parse_section_round_trip_multiple_candidates():
    candidates = [
        ADRCandidate("choice A", "rationale A", "evidence A", "alternative A"),
        ADRCandidate("choice B", "rationale B", "evidence B", "alternative B"),
    ]
    rendered = adr_candidate.render_section(candidates)
    parsed = adr_candidate.parse_section(rendered.splitlines())
    assert parsed == candidates


def test_parse_section_returns_empty_when_section_absent():
    lines = ["# Title", "## Summary", "some summary text", "## What we worked on", "- did stuff"]
    assert adr_candidate.parse_section(lines) == []


def test_parse_section_drops_malformed_entry_silently():
    """A candidate missing a required field is dropped; valid siblings survive."""
    c_valid = _valid()
    rendered_valid = adr_candidate.render_section([c_valid])
    # Inject a malformed entry (missing evidence)
    malformed_lines = ["- **malformed choice**", "  - Why: some reason", "  - Instead of: something"]
    full_lines = rendered_valid.splitlines() + malformed_lines
    parsed = adr_candidate.parse_section(full_lines)
    assert len(parsed) == 1
    assert parsed[0].choice == c_valid.choice


def test_parse_section_recognises_legacy_decisions_made_heading():
    """Legacy ``## Decisions made`` heading parses as ADR candidates too."""
    body = (
        "## Decisions made\n\n"
        "- **chose A**\n"
        "  - Why: reason\n"
        "  - Instead of: B\n"
        "  - Evidence: confirmed at turn 5\n"
    )
    parsed = adr_candidate.parse_section(body.splitlines())
    assert len(parsed) == 1
    assert parsed[0].choice == "chose A"


def test_parse_section_stops_at_next_h2():
    body = (
        "## ADR candidates\n\n"
        "- **choice**\n"
        "  - Why: r\n"
        "  - Instead of: alt\n"
        "  - Evidence: e\n"
        "\n"
        "## What we worked on\n"
        "- **second bullet** — should NOT be parsed as a candidate\n"
    )
    parsed = adr_candidate.parse_section(body.splitlines())
    assert len(parsed) == 1
    assert parsed[0].choice == "choice"


# ---------------------------------------------------------------------------
# tool_schema_property
# ---------------------------------------------------------------------------


def test_tool_schema_property_is_array_with_max_items():
    schema = adr_candidate.tool_schema_property()
    assert schema["type"] == "array"
    assert schema["maxItems"] == 5


def test_tool_schema_property_items_have_four_required_fields():
    schema = adr_candidate.tool_schema_property()
    item = schema["items"]
    assert set(item["required"]) == {"choice", "rationale", "evidence", "alternative_rejected"}


def test_tool_schema_property_disallows_additional_properties():
    schema = adr_candidate.tool_schema_property()
    assert schema["items"]["additionalProperties"] is False


def test_tool_schema_property_has_max_length_on_all_fields():
    schema = adr_candidate.tool_schema_property()
    props = schema["items"]["properties"]
    for field_name in ("choice", "rationale", "evidence", "alternative_rejected"):
        assert "maxLength" in props[field_name], f"missing maxLength on {field_name}"
