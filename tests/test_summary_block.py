"""Tests for lore_curator.summary_block — compose / parse round-trip."""
from __future__ import annotations

from lore_curator import summary_block


def test_compose_lede_only_returns_just_lede():
    assert summary_block.compose("a single sentence lede.", []) == "a single sentence lede."


def test_compose_lede_plus_items_uses_blank_line_separator():
    out = summary_block.compose("lede.", ["first thing", "second thing"])
    assert out == "lede.\n\n- first thing\n- second thing"


def test_compose_drops_empty_and_whitespace_items():
    out = summary_block.compose("lede.", ["real", "  ", "", "another"])
    assert out == "lede.\n\n- real\n- another"


def test_compose_strips_lede_and_item_whitespace():
    out = summary_block.compose("  lede.  ", ["  bullet body  "])
    assert out == "lede.\n\n- bullet body"


def test_compose_no_lede_no_items_returns_empty_string():
    assert summary_block.compose("", []) == ""
    assert summary_block.compose("   ", []) == ""


def test_parse_single_sentence_returns_lede_and_empty_items():
    assert summary_block.parse("just one sentence.") == ("just one sentence.", [])


def test_parse_lede_plus_bullets_splits_correctly():
    text = "lede.\n\n- bullet1\n- bullet2"
    lede, items = summary_block.parse(text)
    assert lede == "lede."
    assert items == ["bullet1", "bullet2"]


def test_parse_legacy_multi_paragraph_prose_returns_full_text_as_lede():
    """Legacy prose Summaries (no bullets) must not throw and must
    round-trip the full text as the lede."""
    legacy = (
        "Old prose paragraph one with several sentences. "
        "It continues for a while.\n\n"
        "And paragraph two adds more context, still no bullets in sight."
    )
    lede, items = summary_block.parse(legacy)
    assert items == []
    # Full prose preserved — exact whitespace stripping at edges is fine.
    assert lede.startswith("Old prose paragraph one")
    assert "paragraph two" in lede


def test_parse_empty_string_returns_empty():
    assert summary_block.parse("") == ("", [])


def test_compose_parse_round_trip_lede_plus_items():
    lede = "outcome lede that fits in one sentence."
    items = ["state-of-world bullet a", "state-of-world bullet b", "third"]
    composed = summary_block.compose(lede, items)
    parsed_lede, parsed_items = summary_block.parse(composed)
    assert parsed_lede == lede
    assert parsed_items == items


def test_compose_parse_round_trip_lede_only():
    composed = summary_block.compose("only a lede.", [])
    parsed_lede, parsed_items = summary_block.parse(composed)
    assert parsed_lede == "only a lede."
    assert parsed_items == []
