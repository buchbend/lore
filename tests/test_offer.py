"""Tests for `.lore.yml` parsing, discovery (walk-up), and fingerprinting."""

from __future__ import annotations

from pathlib import Path

import pytest

from lore_core.offer import (
    FILENAME,
    Offer,
    find_lore_yml,
    offer_fingerprint,
    parse_lore_yml,
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
    assert offer.issues == "--assignee @me --state open"
    assert offer.prs == "--author @me"


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


def test_find_lore_yml_at_cwd(tmp_path: Path) -> None:
    (tmp_path / FILENAME).write_text("wiki: w\nscope: s\n")
    assert find_lore_yml(tmp_path) == tmp_path / FILENAME


def test_find_lore_yml_walks_up(tmp_path: Path) -> None:
    (tmp_path / FILENAME).write_text("wiki: w\nscope: s\n")
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    assert find_lore_yml(deep) == tmp_path / FILENAME


def test_find_lore_yml_returns_none_when_absent(tmp_path: Path) -> None:
    assert find_lore_yml(tmp_path) is None


def test_find_lore_yml_respects_max_depth(tmp_path: Path) -> None:
    (tmp_path / FILENAME).write_text("wiki: w\nscope: s\n")
    deep = tmp_path / "a" / "b" / "c" / "d"
    deep.mkdir(parents=True)
    assert find_lore_yml(deep, max_depth=2) is None


def test_find_lore_yml_picks_nearest(tmp_path: Path) -> None:
    """Nearest ancestor wins when multiple .lore.yml files exist."""
    (tmp_path / FILENAME).write_text("wiki: outer\nscope: o\n")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / FILENAME).write_text("wiki: inner\nscope: i\n")
    found = find_lore_yml(sub)
    assert found == sub / FILENAME


def test_fingerprint_deterministic() -> None:
    o = Offer(wiki="w", scope="a:b", wiki_source="url")
    assert offer_fingerprint(o) == offer_fingerprint(o)


def test_fingerprint_invariant_under_non_routing_fields() -> None:
    a = Offer(wiki="w", scope="a:b", issues="--state open", prs="--author @me")
    b = Offer(wiki="w", scope="a:b", issues="--state closed", prs=None)
    # issues/prs are NOT routing-relevant → fingerprints equal
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
