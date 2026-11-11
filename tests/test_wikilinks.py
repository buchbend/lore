"""Tests for the wikilink-stripping primitive."""

from pathlib import Path

from lore_core.wikilinks import (
    WIKILINK_RE,
    existing_slugs,
    strip_broken_wikilinks,
)


# --- regex sanity ---


def test_wikilink_regex_plain():
    m = WIKILINK_RE.search("see [[my-note]] please")
    assert m and m.group(1) == "my-note" and m.group(2) is None


def test_wikilink_regex_alias():
    m = WIKILINK_RE.search("see [[my-note|the note]] please")
    assert m and m.group(1) == "my-note" and m.group(2) == "the note"


# --- strip_broken_wikilinks ---


def test_strip_keeps_valid_wikilink():
    text = "look at [[real]] please."
    out, n, _ = strip_broken_wikilinks(text, {"real"})
    assert out == text
    assert n == 0


def test_strip_removes_broken_no_alias():
    text = "look at [[ghost]] please."
    out, n, replaced = strip_broken_wikilinks(text, {"real"})
    assert out == "look at ghost please."
    assert n == 1
    assert replaced == ["ghost"]


def test_strip_removes_broken_with_alias_uses_alias():
    text = "look at [[ghost|the ghost]] please."
    out, n, _ = strip_broken_wikilinks(text, set())
    assert out == "look at the ghost please."
    assert n == 1


def test_strip_mixed_valid_and_broken_in_one_line():
    text = "[[real]] and [[ghost]] and [[also-real]]."
    out, n, _ = strip_broken_wikilinks(text, {"real", "also-real"})
    assert out == "[[real]] and ghost and [[also-real]]."
    assert n == 1


def test_strip_preserves_frontmatter_block_verbatim():
    text = (
        "---\n"
        "type: concept\n"
        'superseded_by: "[[ghost]]"\n'
        "---\n"
        "body has [[ghost]] in it.\n"
    )
    out, n, _ = strip_broken_wikilinks(text, set())
    # frontmatter wikilink stays; body wikilink gets stripped
    assert 'superseded_by: "[[ghost]]"' in out
    assert "body has ghost in it." in out
    assert n == 1


def test_strip_skips_fenced_code_blocks():
    text = (
        "before [[ghost]] middle\n"
        "```\n"
        "code with [[ghost]] should stay\n"
        "```\n"
        "after [[ghost]] tail\n"
    )
    out, n, _ = strip_broken_wikilinks(text, set())
    # body broken refs outside the fence are stripped (2 of 3)
    assert "code with [[ghost]] should stay" in out
    assert "before ghost middle" in out
    assert "after ghost tail" in out
    assert n == 2


def test_strip_skips_inline_code_spans():
    text = "prose [[ghost]] and `inline [[ghost]] code` and [[ghost]] tail"
    out, n, _ = strip_broken_wikilinks(text, set())
    assert "`inline [[ghost]] code`" in out
    assert "prose ghost and " in out
    assert " and ghost tail" in out
    assert n == 2


def test_strip_idempotent():
    text = "before [[ghost]] middle [[real]] tail"
    out1, _, _ = strip_broken_wikilinks(text, {"real"})
    out2, n2, _ = strip_broken_wikilinks(out1, {"real"})
    assert out1 == out2
    assert n2 == 0


def test_strip_returns_unchanged_on_no_wikilinks():
    text = "plain prose with `code`.\n"
    out, n, replaced = strip_broken_wikilinks(text, set())
    assert out == text
    assert n == 0
    assert replaced == []


def test_strip_handles_no_frontmatter_doc():
    text = "no frontmatter; [[ghost]] only.\n"
    out, n, _ = strip_broken_wikilinks(text, set())
    assert out == "no frontmatter; ghost only.\n"
    assert n == 1


# --- existing_slugs ---


def test_existing_slugs_collects_md_stems(tmp_path: Path):
    (tmp_path / "concepts").mkdir()
    (tmp_path / "concepts" / "alpha.md").write_text("x")
    (tmp_path / "concepts" / "beta.md").write_text("x")
    (tmp_path / "_index.md").write_text("x")  # underscore-prefix → skip
    (tmp_path / "_recent.md").write_text("x")  # underscore-prefix → skip
    (tmp_path / "concepts" / "gamma.txt").write_text("x")  # non-md → skip
    slugs = existing_slugs(tmp_path)
    assert slugs == {"alpha", "beta"}


def test_existing_slugs_includes_frontmatter_aliases(tmp_path: Path):
    """Notes with ``aliases:`` in frontmatter contribute extra entries.

    Required so that when a note is renamed and leaves an alias trail,
    pre-existing ``[[old-stem]]`` references still resolve.
    """
    (tmp_path / "sessions").mkdir()
    (tmp_path / "sessions" / "01-1432-auth-handler-refactor.md").write_text(
        "---\n"
        "type: session\n"
        "aliases:\n"
        "  - 01-1432-auth\n"
        "  - 01-1432-session-lore-1432\n"
        "---\n"
        "body\n"
    )
    (tmp_path / "sessions" / "no-aliases.md").write_text(
        "---\ntype: session\n---\nbody\n"
    )
    slugs = existing_slugs(tmp_path)
    assert "01-1432-auth-handler-refactor" in slugs
    assert "01-1432-auth" in slugs  # alias resolves
    assert "01-1432-session-lore-1432" in slugs  # alias resolves
    assert "no-aliases" in slugs


def test_existing_slugs_handles_string_alias_form(tmp_path: Path):
    """Frontmatter ``aliases: foo`` (string, not list) also works."""
    (tmp_path / "n.md").write_text(
        "---\ntype: session\naliases: legacy-stem\n---\nbody\n"
    )
    slugs = existing_slugs(tmp_path)
    assert slugs == {"n", "legacy-stem"}
