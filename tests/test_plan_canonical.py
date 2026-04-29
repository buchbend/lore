"""Tests for the canonical step heading + ID format.

The canonical contract: ``### step-<N>: <title>`` heading + ``step-<N>``
ID, used everywhere on disk and in trailers/wikilinks. This module is
the single source of truth — three other modules (parser, writer,
registry) consume from here.
"""
from __future__ import annotations

import pytest

from lore_core.plans.canonical import (
    CANONICAL_STEP_RE,
    LEGACY_STEP_HEADING_RE,
    canonicalize_step_id,
    extract_canonical_step_ids,
    extract_step_ids,
    format_canonical_heading,
    is_legacy_step_id,
    parse_step_id_ordinal,
    step_id_for,
)
from lore_core.plans.types import PlanStep


class TestStepIdConstruction:
    def test_step_id_for_basic(self) -> None:
        assert step_id_for(1) == "step-1"
        assert step_id_for(12) == "step-12"

    def test_step_id_for_rejects_zero_and_negative(self) -> None:
        with pytest.raises(ValueError, match=">= 1"):
            step_id_for(0)
        with pytest.raises(ValueError, match=">= 1"):
            step_id_for(-1)

    def test_parse_ordinal_canonical(self) -> None:
        assert parse_step_id_ordinal("step-1") == 1
        assert parse_step_id_ordinal("step-42") == 42

    def test_parse_ordinal_legacy(self) -> None:
        assert parse_step_id_ordinal("s1") == 1
        assert parse_step_id_ordinal("s99") == 99

    def test_parse_ordinal_case_insensitive(self) -> None:
        assert parse_step_id_ordinal("STEP-3") == 3
        assert parse_step_id_ordinal("S5") == 5

    def test_parse_ordinal_returns_none_for_garbage(self) -> None:
        assert parse_step_id_ordinal("") is None
        assert parse_step_id_ordinal("foo") is None
        assert parse_step_id_ordinal("step") is None
        assert parse_step_id_ordinal("step-") is None
        assert parse_step_id_ordinal("step-1a") is None


class TestLegacyDetectionAndCanonicalization:
    def test_is_legacy(self) -> None:
        assert is_legacy_step_id("s1")
        assert is_legacy_step_id("s12")
        assert is_legacy_step_id("S5")  # case-insensitive
        assert not is_legacy_step_id("step-1")
        assert not is_legacy_step_id("")
        assert not is_legacy_step_id("foo")

    def test_canonicalize_legacy_to_canonical(self) -> None:
        assert canonicalize_step_id("s1") == "step-1"
        assert canonicalize_step_id("s42") == "step-42"

    def test_canonicalize_already_canonical_is_idempotent(self) -> None:
        assert canonicalize_step_id("step-1") == "step-1"
        assert canonicalize_step_id("step-99") == "step-99"

    def test_canonicalize_returns_unrecognized_unchanged(self) -> None:
        assert canonicalize_step_id("foo") == "foo"
        assert canonicalize_step_id("") == ""


class TestHeadingRegexes:
    def test_canonical_re_matches_canonical_heading(self) -> None:
        assert CANONICAL_STEP_RE.match("### step-1: Title")
        assert CANONICAL_STEP_RE.match("### step-42: A really long title with colons: foo")
        # Case-insensitive read.
        assert CANONICAL_STEP_RE.match("### STEP-3: title")

    def test_canonical_re_rejects_legacy(self) -> None:
        assert not CANONICAL_STEP_RE.match("### s1: Title")

    def test_canonical_re_rejects_non_step_headings(self) -> None:
        assert not CANONICAL_STEP_RE.match("### Phase 1 — Foundation")
        assert not CANONICAL_STEP_RE.match("## step-1: wrong level")
        assert not CANONICAL_STEP_RE.match("### step- 1")  # space breaks it

    def test_legacy_re_matches_legacy_only(self) -> None:
        assert LEGACY_STEP_HEADING_RE.match("### s1: Title")
        assert LEGACY_STEP_HEADING_RE.match("### S5: case-insensitive read")
        assert not LEGACY_STEP_HEADING_RE.match("### step-1: Title")


class TestFormatCanonicalHeading:
    def test_renders_canonical_heading(self) -> None:
        step = PlanStep(id="step-1", title="AND-then-OR FTS query semantics", body="...")
        assert format_canonical_heading(step) == "### step-1: AND-then-OR FTS query semantics"

    def test_canonicalizes_legacy_id_on_emit(self) -> None:
        # Even if a caller passes a legacy ID, the writer normalizes.
        step = PlanStep(id="s3", title="Legacy ID renamed", body="...")
        assert format_canonical_heading(step) == "### step-3: Legacy ID renamed"

    def test_falls_back_to_id_when_title_empty(self) -> None:
        step = PlanStep(id="step-2", title="", body="body")
        assert format_canonical_heading(step) == "### step-2: step-2"

    def test_strips_whitespace_from_title(self) -> None:
        step = PlanStep(id="step-1", title="  padded  ", body="")
        assert format_canonical_heading(step) == "### step-1: padded"


class TestExtractStepIds:
    def test_extracts_canonical_in_order(self) -> None:
        body = "## Steps\n\n### step-1: a\nbody\n### step-2: b\n### step-3: c\n"
        assert extract_step_ids(body) == ["step-1", "step-2", "step-3"]

    def test_extracts_legacy_in_order(self) -> None:
        body = "### s1: a\n### s2: b\n"
        assert extract_step_ids(body) == ["s1", "s2"]

    def test_mixed_canonical_and_legacy_returns_verbatim(self) -> None:
        # During transition some plans may have been hand-edited.
        body = "### step-1: new\n### s2: old\n"
        assert extract_step_ids(body) == ["step-1", "s2"]

    def test_extract_canonical_normalizes_legacy(self) -> None:
        body = "### s1: a\n### s2: b\n"
        assert extract_canonical_step_ids(body) == ["step-1", "step-2"]

    def test_ignores_non_step_headings(self) -> None:
        body = "## Phase 1 — Foundation\n### Some other heading\n### step-1: real\n"
        assert extract_step_ids(body) == ["step-1"]

    def test_empty_body_returns_empty_list(self) -> None:
        assert extract_step_ids("") == []
        assert extract_canonical_step_ids("") == []
