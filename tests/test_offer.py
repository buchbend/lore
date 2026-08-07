"""Tests for `.lore.yml` parsing, discovery (walk-up), and fingerprinting."""

from __future__ import annotations

from pathlib import Path

import pytest

from lore_core.offer import (
    FILENAME,
    Offer,
    find_lore_yml,
    find_lore_yml_raw,
    offer_fingerprint,
    parse_lore_yml,
    validate_offer_raw,
)


def test_parse_full_offer(tmp_path: Path) -> None:
    (tmp_path / FILENAME).write_text(
        "wiki: team-alpha\n"
        "scope: ccat:data-center:computers\n"
        "backend: github\n"
        "wiki_source: git@github.com:team/alpha-wiki.git\n"
        "issues: --assignee @me --state open\n"
        "prs: --author @me\n"
    )
    offer = parse_lore_yml(tmp_path / FILENAME)
    assert offer is not None
    assert offer.wiki == "team-alpha"
    assert offer.scope == "ccat:data-center:computers"
    assert offer.backend == "github"
    assert offer.wiki_source == "git@github.com:team/alpha-wiki.git"
    # `issues:`/`prs:` lost their reader with the gh list wrappers. A file
    # written against an older Lore still parses — the keys are ignored, not
    # an error, so an attached repo keeps working untouched.
    assert not hasattr(offer, "issues")
    assert not hasattr(offer, "prs")


def test_parse_minimal_offer(tmp_path: Path) -> None:
    (tmp_path / FILENAME).write_text("wiki: w\nscope: a:b\n")
    offer = parse_lore_yml(tmp_path / FILENAME)
    assert offer is not None
    assert offer.wiki == "w"
    assert offer.scope == "a:b"
    assert offer.backend == "none"          # default
    assert offer.wiki_source is None


def test_parse_missing_file_returns_none(tmp_path: Path) -> None:
    assert parse_lore_yml(tmp_path / FILENAME) is None


def test_parse_missing_wiki_returns_none(tmp_path: Path) -> None:
    (tmp_path / FILENAME).write_text("scope: a:b\n")   # wiki missing
    assert parse_lore_yml(tmp_path / FILENAME) is None


def test_parse_missing_scope_returns_none(tmp_path: Path) -> None:
    (tmp_path / FILENAME).write_text("wiki: w\n")      # scope missing
    assert parse_lore_yml(tmp_path / FILENAME) is None


def test_parse_empty_wiki_returns_none(tmp_path: Path) -> None:
    (tmp_path / FILENAME).write_text("wiki: ''\nscope: a:b\n")
    assert parse_lore_yml(tmp_path / FILENAME) is None


def test_parse_malformed_yaml_returns_none(tmp_path: Path) -> None:
    (tmp_path / FILENAME).write_text("wiki: [unclosed\n")
    assert parse_lore_yml(tmp_path / FILENAME) is None


def test_parse_list_root_returns_none(tmp_path: Path) -> None:
    """Top-level must be a mapping; a list or scalar is invalid."""
    (tmp_path / FILENAME).write_text("- just-a-list\n")
    assert parse_lore_yml(tmp_path / FILENAME) is None


def test_parse_inherit_true(tmp_path: Path) -> None:
    (tmp_path / FILENAME).write_text("wiki: w\nscope: s\ninherit: true\n")
    offer = parse_lore_yml(tmp_path / FILENAME)
    assert offer is not None
    assert offer.inherit is True


def test_parse_inherit_default_false(tmp_path: Path) -> None:
    (tmp_path / FILENAME).write_text("wiki: w\nscope: s\n")
    offer = parse_lore_yml(tmp_path / FILENAME)
    assert offer is not None
    assert offer.inherit is False


def test_parse_inherit_string_yes_is_false(tmp_path: Path) -> None:
    """Only literal Python ``True`` counts. Strings, ints, etc. are False."""
    (tmp_path / FILENAME).write_text("wiki: w\nscope: s\ninherit: 'yes'\n")
    offer = parse_lore_yml(tmp_path / FILENAME)
    assert offer is not None
    assert offer.inherit is False


def test_find_lore_yml_at_cwd(tmp_path: Path) -> None:
    (tmp_path / FILENAME).write_text("wiki: w\nscope: s\n")
    found = find_lore_yml(tmp_path)
    assert found is not None
    path, offer = found
    assert path == tmp_path / FILENAME
    assert offer.wiki == "w"


def test_find_lore_yml_returns_none_when_absent(tmp_path: Path) -> None:
    assert find_lore_yml(tmp_path) is None


def test_find_lore_yml_respects_max_depth(tmp_path: Path) -> None:
    (tmp_path / FILENAME).write_text("wiki: w\nscope: s\ninherit: true\n")
    deep = tmp_path / "a" / "b" / "c" / "d"
    deep.mkdir(parents=True)
    assert find_lore_yml(deep, max_depth=2) is None


def test_find_lore_yml_ancestor_without_inherit_returns_none(tmp_path: Path) -> None:
    """Walk-up no longer auto-inherits — issue #24."""
    (tmp_path / FILENAME).write_text("wiki: w\nscope: s\n")
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    assert find_lore_yml(deep) is None


def test_find_lore_yml_ancestor_with_inherit_applies(tmp_path: Path) -> None:
    """`inherit: true` opts back in to subtree application."""
    (tmp_path / FILENAME).write_text("wiki: w\nscope: s\ninherit: true\n")
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    found = find_lore_yml(deep)
    assert found is not None
    path, offer = found
    assert path == tmp_path / FILENAME
    assert offer.inherit is True


def test_find_lore_yml_child_shadows_inheriting_parent(tmp_path: Path) -> None:
    """Child's own .lore.yml always wins over an inheriting parent."""
    (tmp_path / FILENAME).write_text("wiki: outer\nscope: o\ninherit: true\n")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / FILENAME).write_text("wiki: inner\nscope: i\n")
    found = find_lore_yml(sub)
    assert found is not None
    path, offer = found
    assert path == sub / FILENAME
    assert offer.wiki == "inner"


def test_find_lore_yml_malformed_at_cwd_shadows_parent(tmp_path: Path) -> None:
    """A present-but-malformed .lore.yml is a stop signal — preserves the
    'broken offer doesn't trigger prompts' guarantee even when a parent
    has inherit: true."""
    (tmp_path / FILENAME).write_text("wiki: outer\nscope: o\ninherit: true\n")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / FILENAME).write_text("wiki: [unclosed\n")
    assert find_lore_yml(sub) is None


def test_find_lore_yml_raw_no_policy(tmp_path: Path) -> None:
    """`find_lore_yml_raw` ignores the inherit policy — diagnostic only."""
    (tmp_path / FILENAME).write_text("wiki: w\nscope: s\n")  # no inherit
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    assert find_lore_yml_raw(deep) == tmp_path / FILENAME


def test_fingerprint_deterministic() -> None:
    o = Offer(wiki="w", scope="a:b", wiki_source="url")
    assert offer_fingerprint(o) == offer_fingerprint(o)


def test_fingerprint_invariant_under_non_routing_fields() -> None:
    a = Offer(wiki="w", scope="a:b", backend="github")
    b = Offer(wiki="w", scope="a:b", backend="none")
    # backend is NOT routing-relevant → fingerprints equal
    assert offer_fingerprint(a) == offer_fingerprint(b)


def test_fingerprint_invariant_under_inherit_toggle() -> None:
    """Toggling `inherit` must not invalidate prior accept/decline."""
    a = Offer(wiki="w", scope="a:b", inherit=False)
    b = Offer(wiki="w", scope="a:b", inherit=True)
    assert offer_fingerprint(a) == offer_fingerprint(b)


def test_fingerprint_changes_on_wiki_change() -> None:
    a = Offer(wiki="w1", scope="a:b")
    b = Offer(wiki="w2", scope="a:b")
    assert offer_fingerprint(a) != offer_fingerprint(b)


def test_fingerprint_changes_on_scope_change() -> None:
    a = Offer(wiki="w", scope="a:b")
    b = Offer(wiki="w", scope="a:c")
    assert offer_fingerprint(a) != offer_fingerprint(b)


def test_fingerprint_changes_on_wiki_source_change() -> None:
    a = Offer(wiki="w", scope="a:b", wiki_source="url1")
    b = Offer(wiki="w", scope="a:b", wiki_source="url2")
    assert offer_fingerprint(a) != offer_fingerprint(b)


# ---- validate_offer_raw ----

def test_validate_offer_raw_valid_is_empty(tmp_path: Path) -> None:
    (tmp_path / FILENAME).write_text("wiki: w\nscope: a:b\nbackend: github\n")
    assert validate_offer_raw(tmp_path / FILENAME) == []


def test_validate_offer_raw_unknown_key_names_it(tmp_path: Path) -> None:
    (tmp_path / FILENAME).write_text("wiki: w\nscope: a:b\nbakcend: github\n")
    errors = validate_offer_raw(tmp_path / FILENAME)
    assert len(errors) == 1
    assert "bakcend" in errors[0]
    assert "backend" in errors[0]  # suggestion


def test_validate_offer_raw_type_mismatch(tmp_path: Path) -> None:
    (tmp_path / FILENAME).write_text("wiki: w\nscope: a:b\ninherit: not-a-bool\n")
    errors = validate_offer_raw(tmp_path / FILENAME)
    assert len(errors) == 1
    assert "inherit" in errors[0]


def test_validate_offer_raw_missing_wiki(tmp_path: Path) -> None:
    (tmp_path / FILENAME).write_text("scope: a:b\n")
    errors = validate_offer_raw(tmp_path / FILENAME)
    assert any("wiki" in e for e in errors)


def test_validate_offer_raw_missing_file_is_empty(tmp_path: Path) -> None:
    """Absence is `parse_lore_yml`'s job to report — not a validation error."""
    assert validate_offer_raw(tmp_path / FILENAME) == []


def test_validate_offer_raw_malformed_yaml_is_empty(tmp_path: Path) -> None:
    """Malformed YAML is treated as absence, same as `parse_lore_yml`."""
    (tmp_path / FILENAME).write_text("wiki: [unclosed\n")
    assert validate_offer_raw(tmp_path / FILENAME) == []
