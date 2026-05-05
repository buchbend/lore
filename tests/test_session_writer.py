"""Direct unit tests for lore_core.session_writer.

session_writer is exercised indirectly by test_session_filer.py and
test_synthesis.py, but the parse/render/merge primitives benefit from
isolated round-trip coverage — especially after step-5 of plan
``yes-do-that-keen-yeti`` adds the conditional ``## Discussion`` section
that complements ``## Decisions made`` / ``## What we worked on`` for
discussion-shape notes.
"""
from __future__ import annotations

from lore_core.session_writer import (
    BodySections,
    merge_body_sections,
    parse_body_sections,
    render_body_sections,
)


# ---------------------------------------------------------------------------
# Discussion section round-trip
# ---------------------------------------------------------------------------


def test_render_emits_discussion_section_when_non_empty():
    s = BodySections(
        title="Sketched docs structure",
        summary="A short summary.",
        decisions=[],
        worked_on=[],
        loose_ends=[],
        commits=[],
        issues_opened=[],
        issues_closed=[],
        discussion=["- considered Diátaxis", "- evaluated split"],
    )
    out = render_body_sections(s)
    assert "## Discussion" in out
    assert "- considered Diátaxis" in out
    # Empty work-shape sections must NOT appear when discussion is present.
    assert "## Decisions made" not in out
    assert "## What we worked on" not in out


def test_render_omits_discussion_when_empty():
    s = BodySections(
        title="Fixed something",
        summary="A short summary.",
        decisions=["- chose A over B"],
        worked_on=["- patched X"],
        loose_ends=[],
        commits=[],
        issues_opened=[],
        issues_closed=[],
        discussion=[],
    )
    out = render_body_sections(s)
    assert "## Discussion" not in out
    assert "## Decisions made" in out
    assert "## What we worked on" in out


def test_discussion_renders_between_summary_and_decisions():
    """Order matters for human reading: Summary → Discussion → Decisions
    → What we worked on → Activity → Loose ends. Discussion sits next
    to Summary because in non-work shape it carries the narrative."""
    s = BodySections(
        title="Mixed shape",
        summary="Summary text.",
        decisions=["- decision A"],
        worked_on=["- work A"],
        loose_ends=[],
        commits=[],
        issues_opened=[],
        issues_closed=[],
        discussion=["- discussion A"],
    )
    out = render_body_sections(s)
    summary_pos = out.find("## Summary")
    discussion_pos = out.find("## Discussion")
    decisions_pos = out.find("## Decisions made")
    worked_on_pos = out.find("## What we worked on")
    assert -1 < summary_pos < discussion_pos < decisions_pos < worked_on_pos


def test_parse_recovers_discussion_section():
    body = (
        "# Title\n\n"
        "## Summary\n\n"
        "A summary.\n\n"
        "## Discussion\n\n"
        "- considered X\n"
        "- evaluated Y\n\n"
        "## Loose ends\n\n"
        "- Z remained untested.\n"
    )
    parsed = parse_body_sections(body)
    assert parsed.title == "Title"
    assert parsed.discussion == ["- considered X", "- evaluated Y"]
    assert parsed.loose_ends == ["- Z remained untested."]


def test_round_trip_preserves_discussion_bullets():
    original = BodySections(
        title="Round trip",
        summary="One line.",
        decisions=[],
        worked_on=[],
        loose_ends=["- thread Z left open."],
        commits=[],
        issues_opened=[],
        issues_closed=[],
        discussion=["- alpha", "- beta"],
    )
    rendered = render_body_sections(original)
    parsed = parse_body_sections(rendered)
    assert parsed.discussion == ["- alpha", "- beta"]
    assert parsed.loose_ends == ["- thread Z left open."]


# ---------------------------------------------------------------------------
# Mixed-kind merge (step-7 territory: documented behavior)
# ---------------------------------------------------------------------------


def test_merge_unions_discussion_with_existing_worked_on():
    """A discussion-shape part-1 followed by a work-shape part-2 (or vice
    versa) merging into the same note produces the union of section
    types. This is the documented limitation per step-7 of the plan —
    we don't try to reconcile, we render both."""
    existing = BodySections(
        title="Original",
        summary="First framing.",
        decisions=[],
        worked_on=[],
        loose_ends=[],
        commits=[],
        issues_opened=[],
        issues_closed=[],
        discussion=["- explored A", "- explored B"],
    )
    new = BodySections(
        title="",
        summary="",
        decisions=["- chose A over B"],
        worked_on=["- patched A"],
        loose_ends=[],
        commits=[],
        issues_opened=[],
        issues_closed=[],
        discussion=[],
    )
    merged = merge_body_sections(existing, new)
    out = render_body_sections(merged)
    # All three section types coexist — that's the documented behavior.
    assert "## Discussion" in out
    assert "## Decisions made" in out
    assert "## What we worked on" in out


def test_merge_dedupes_discussion_lines():
    existing = BodySections(
        title="x", summary="", decisions=[], worked_on=[], loose_ends=[],
        commits=[], issues_opened=[], issues_closed=[],
        discussion=["- alpha", "- beta"],
    )
    new = BodySections(
        title="", summary="", decisions=[], worked_on=[], loose_ends=[],
        commits=[], issues_opened=[], issues_closed=[],
        discussion=["- beta", "- gamma"],  # beta is a dup
    )
    merged = merge_body_sections(existing, new)
    assert merged.discussion == ["- alpha", "- beta", "- gamma"]


# ---------------------------------------------------------------------------
# Default empty discussion is omitted (back-compat for existing call
# sites that don't pass the new field).
# ---------------------------------------------------------------------------


def test_default_empty_discussion_omitted_from_render():
    """Existing callers that don't pass ``discussion=`` get the empty
    default — and the renderer omits the section. Vital so step-1..3
    don't accidentally start emitting `## Discussion` headers in
    every existing work-shape note."""
    s = BodySections(
        title="Existing call site",
        summary="x",
        decisions=[],
        worked_on=["- y"],
        loose_ends=[],
        commits=[],
        issues_opened=[],
        issues_closed=[],
        # discussion omitted — uses the NamedTuple default
    )
    out = render_body_sections(s)
    assert "## Discussion" not in out
    assert "## What we worked on" in out
