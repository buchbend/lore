"""Per-wiki scoping — wikis are portable units, cross-wiki refs are dangles.

Wikilink resolution in Lore is per-wiki by design (see
``feedback_wiki_portability.md``). A wikilink in ``wiki/ccat/`` that
resolves only via ``wiki/private/`` breaks the moment the ccat wiki
is pulled into a different vault context. Validators and migrations
treat such references as broken.
"""

from pathlib import Path

import pytest

from lore_core.migrate import migrate_strip_broken_wikilinks
from lore_core.wikilinks import existing_slugs


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Two-wiki vault: ``ccat`` and ``private``."""
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    for wname in ("ccat", "private"):
        (tmp_path / "wiki" / wname / "concepts").mkdir(parents=True)
        (tmp_path / "wiki" / wname / "decisions").mkdir(parents=True)
    return tmp_path


def _note(wiki: Path, sub: str, slug: str, body: str) -> Path:
    p = wiki / sub / f"{slug}.md"
    p.write_text(
        f"---\nschema_version: 2\ntype: concept\n"
        f"created: 2026-04-29\nlast_reviewed: 2026-04-29\n"
        f"description: x\ntags: []\n---\n{body}"
    )
    return p


def test_existing_slugs_is_per_wiki_scoped(vault: Path):
    ccat = vault / "wiki" / "ccat"
    private = vault / "wiki" / "private"
    _note(ccat, "concepts", "alpha", "x")
    _note(private, "concepts", "beta", "x")

    # Each wiki sees only its own slugs — siblings stay invisible.
    assert existing_slugs(ccat) == {"alpha"}
    assert existing_slugs(private) == {"beta"}


def test_migration_strips_cross_wiki_reference(vault: Path):
    ccat = vault / "wiki" / "ccat"
    private = vault / "wiki" / "private"
    # `claude-md-boundary` lives in private/, referenced from ccat/.
    # Per-wiki design: this is a dangle from ccat's perspective and
    # must be stripped, because ccat is meant to be portable.
    _note(private, "decisions", "claude-md-boundary", "i live in private.")
    _note(
        ccat,
        "decisions",
        "docs-strategy",
        "see [[claude-md-boundary]] for the boundary rule.",
    )

    result = migrate_strip_broken_wikilinks(dry_run=False)
    assert result["replacements"] == 1
    after = (ccat / "decisions" / "docs-strategy.md").read_text()
    assert "[[claude-md-boundary]]" not in after
    assert "see claude-md-boundary for the boundary rule." in after
    # The note in private/ that legitimately owns that slug stays untouched.
    assert (private / "decisions" / "claude-md-boundary.md").read_text() != ""
