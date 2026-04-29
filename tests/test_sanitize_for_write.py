"""Forward guard — sanitize_for_write strips broken links per-wiki."""

from pathlib import Path

import pytest

from lore_core.wikilinks import sanitize_for_write


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    for wname in ("ccat", "private"):
        (tmp_path / "wiki" / wname / "concepts").mkdir(parents=True)
    (tmp_path / "wiki" / "private" / "concepts" / "private-only.md").write_text("x")
    (tmp_path / "wiki" / "ccat" / "concepts" / "ccat-only.md").write_text("x")
    return tmp_path


def test_sanitize_keeps_local_strips_broken(vault: Path):
    wiki_root = vault / "wiki" / "private"
    text = "see [[private-only]] and [[ghost]] please."
    out = sanitize_for_write(text, wiki_root)
    assert out == "see [[private-only]] and ghost please."


def test_sanitize_strips_cross_wiki_references(vault: Path):
    """Cross-wiki refs are broken by design — wikis are portable units."""
    wiki_root = vault / "wiki" / "private"
    # `ccat-only` lives in the ccat wiki — from private's perspective
    # it's a dangle that would break if private were extracted.
    text = "the [[ccat-only]] reference must be stripped."
    out = sanitize_for_write(text, wiki_root)
    assert out == "the ccat-only reference must be stripped."


def test_sanitize_handles_lone_wiki(tmp_path: Path):
    wiki = tmp_path / "loose-wiki"
    (wiki / "concepts").mkdir(parents=True)
    (wiki / "concepts" / "alpha.md").write_text("x")

    text = "[[alpha]] valid; [[ghost]] not."
    out = sanitize_for_write(text, wiki)
    assert out == "[[alpha]] valid; ghost not."
