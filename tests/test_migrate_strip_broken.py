"""End-to-end test for `lore migrate --strip-broken-wikilinks`."""

from pathlib import Path

import pytest

from lore_core.migrate import migrate_strip_broken_wikilinks


def _make_wiki(root: Path, name: str) -> Path:
    wiki = root / "wiki" / name
    (wiki / "concepts").mkdir(parents=True)
    return wiki


def _note(wiki: Path, sub: str, slug: str, body: str) -> Path:
    p = wiki / sub / f"{slug}.md"
    p.write_text(
        f"---\nschema_version: 2\ntype: concept\n"
        f"created: 2026-04-29\nlast_reviewed: 2026-04-29\n"
        f"description: x\ntags: []\n---\n{body}"
    )
    return p


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    return tmp_path


def test_migration_dry_run_does_not_write(vault: Path):
    wiki = _make_wiki(vault, "private")
    _note(wiki, "concepts", "alpha", "see [[real]] and [[ghost]] now.")
    _note(wiki, "concepts", "real", "i am real.")

    before = (wiki / "concepts" / "alpha.md").read_text()
    result = migrate_strip_broken_wikilinks(dry_run=True)

    assert result["replacements"] == 1
    assert result["files"] == 1
    assert (wiki / "concepts" / "alpha.md").read_text() == before


def test_migration_apply_writes_changes(vault: Path):
    wiki = _make_wiki(vault, "private")
    _note(wiki, "concepts", "alpha", "see [[real]] and [[ghost]] now.")
    _note(wiki, "concepts", "real", "i am real.")

    result = migrate_strip_broken_wikilinks(dry_run=False)
    assert result["replacements"] == 1
    after = (wiki / "concepts" / "alpha.md").read_text()
    assert "[[real]]" in after, "valid wikilink preserved"
    assert "[[ghost]]" not in after
    assert "and ghost now." in after


def test_migration_idempotent(vault: Path):
    wiki = _make_wiki(vault, "private")
    _note(wiki, "concepts", "alpha", "[[ghost]] [[gone]] and [[real]]")
    _note(wiki, "concepts", "real", "x")

    first = migrate_strip_broken_wikilinks(dry_run=False)
    second = migrate_strip_broken_wikilinks(dry_run=False)
    assert first["replacements"] == 2
    assert second["replacements"] == 0


def test_migration_reports_top_targets(vault: Path):
    wiki = _make_wiki(vault, "private")
    _note(wiki, "concepts", "n1", "[[Curator B]] said [[ghost]] then [[Curator B]]")
    _note(wiki, "concepts", "n2", "more [[Curator B]] here")

    result = migrate_strip_broken_wikilinks(dry_run=True)
    assert result["by_target"]["Curator B"] == 3
    assert result["by_target"]["ghost"] == 1


def test_migration_skips_underscore_files(vault: Path):
    wiki = _make_wiki(vault, "private")
    # Index files (regenerable) must never be migrated even if they
    # contain broken wikilinks — they're owned by the linter.
    (wiki / "_index.txt").write_text("[[ghost]] is just an index entry")
    _note(wiki, "concepts", "real", "x")

    result = migrate_strip_broken_wikilinks(dry_run=False)
    assert result["replacements"] == 0
    assert "[[ghost]]" in (wiki / "_index.txt").read_text()
