"""Tests for ScopesFile: flat ID-path scope tree, ingest_chain,
resolve_wiki inheritance, rename, reparent, remove."""

from __future__ import annotations

from pathlib import Path

import pytest

from lore_core.state.scopes import (
    ScopeConflict,
    ScopeEntry,
    ScopesFile,
    ancestors_of,
    parent_of,
)


@pytest.fixture
def lore_root(tmp_path: Path) -> Path:
    (tmp_path / ".lore").mkdir()
    return tmp_path


# ---- helpers ----

def test_parent_of_root() -> None:
    assert parent_of("ccat") is None


def test_parent_of_descendant() -> None:
    assert parent_of("ccat:data-center:computers") == "ccat:data-center"


def test_ancestors_of() -> None:
    assert ancestors_of("ccat:a:b") == ["ccat", "ccat:a", "ccat:a:b"]


def test_ancestors_of_root() -> None:
    assert ancestors_of("ccat") == ["ccat"]


# ---- load/save ----

def test_empty_load(lore_root: Path) -> None:
    sf = ScopesFile(lore_root)
    sf.load()
    assert sf.all_ids() == []


def test_load_tolerates_malformed(lore_root: Path) -> None:
    (lore_root / ".lore" / "scopes.json").write_text("{broken")
    sf = ScopesFile(lore_root)
    sf.load()
    assert sf.all_ids() == []


def test_save_reload_roundtrip(lore_root: Path) -> None:
    sf = ScopesFile(lore_root)
    sf.load()
    sf.ingest_chain("ccat:data-center:computers", wiki="team-alpha")
    sf.save()

    sf2 = ScopesFile(lore_root)
    sf2.load()
    assert set(sf2.all_ids()) == {"ccat", "ccat:data-center", "ccat:data-center:computers"}
    assert sf2.get("ccat").wiki == "team-alpha"
    assert sf2.get("ccat:data-center").wiki is None


# ---- ingest_chain ----

def test_ingest_chain_creates_all_ancestors(lore_root: Path) -> None:
    sf = ScopesFile(lore_root)
    sf.load()
    created = sf.ingest_chain("a:b:c", wiki="w")
    assert created == ["a", "a:b", "a:b:c"]


def test_ingest_chain_is_idempotent(lore_root: Path) -> None:
    sf = ScopesFile(lore_root)
    sf.load()
    sf.ingest_chain("a:b:c", wiki="w")
    created = sf.ingest_chain("a:b:c", wiki="w")
    assert created == []


def test_ingest_chain_adds_only_new_leaf(lore_root: Path) -> None:
    sf = ScopesFile(lore_root)
    sf.load()
    sf.ingest_chain("a:b", wiki="w")
    created = sf.ingest_chain("a:b:c", wiki="w")
    assert created == ["a:b:c"]


def test_ingest_chain_conflict_on_root_wiki(lore_root: Path) -> None:
    sf = ScopesFile(lore_root)
    sf.load()
    sf.ingest_chain("a:b", wiki="w1")
    with pytest.raises(ScopeConflict) as exc:
        sf.ingest_chain("a:c", wiki="w2")
    assert exc.value.scope_root == "a"
    assert exc.value.existing_wiki == "w1"
    assert exc.value.incoming_wiki == "w2"


def test_ingest_chain_adopts_wiki_for_existing_wikiless_root(lore_root: Path) -> None:
    """If the root was created without a wiki (rare: direct edit), an
    incoming offer adopts it into the root."""
    sf = ScopesFile(lore_root)
    sf.load()
    sf.set_entry("a", ScopeEntry())
    sf.ingest_chain("a:b", wiki="w")
    assert sf.get("a").wiki == "w"


# ---- resolve_wiki ----

def test_resolve_wiki_direct(lore_root: Path) -> None:
    sf = ScopesFile(lore_root)
    sf.load()
    sf.ingest_chain("a", wiki="w")
    assert sf.resolve_wiki("a") == "w"


def test_resolve_wiki_inherited(lore_root: Path) -> None:
    sf = ScopesFile(lore_root)
    sf.load()
    sf.ingest_chain("a:b:c", wiki="w")
    assert sf.resolve_wiki("a:b:c") == "w"
    assert sf.resolve_wiki("a:b") == "w"


def test_resolve_wiki_unknown_scope(lore_root: Path) -> None:
    sf = ScopesFile(lore_root)
    sf.load()
    assert sf.resolve_wiki("unknown") is None


def test_resolve_wiki_override_descendant(lore_root: Path) -> None:
    sf = ScopesFile(lore_root)
    sf.load()
    sf.ingest_chain("a:b:c", wiki="w1")
    sf.set_entry("a:b", ScopeEntry(wiki="w2"))
    assert sf.resolve_wiki("a:b:c") == "w2"
    assert sf.resolve_wiki("a") == "w1"


# ---- descendants ----

def test_descendants(lore_root: Path) -> None:
    sf = ScopesFile(lore_root)
    sf.load()
    sf.ingest_chain("a:b:c", wiki="w")
    sf.ingest_chain("a:b:d", wiki="w")
    sf.ingest_chain("a:x", wiki="w")
    assert sf.descendants("a:b") == ["a:b:c", "a:b:d"]
    assert sf.descendants("a") == ["a:b", "a:b:c", "a:b:d", "a:x"]
    assert sf.descendants("a:b:c") == []


# ---- rename ----

def test_rename_leaf(lore_root: Path) -> None:
    sf = ScopesFile(lore_root)
    sf.load()
    sf.ingest_chain("a:b", wiki="w")
    mapping = sf.rename("a:b", "a:renamed")
    assert mapping == [("a:b", "a:renamed")]
    assert sf.get("a:b") is None
    assert sf.get("a:renamed") is not None


def test_rename_subtree(lore_root: Path) -> None:
    sf = ScopesFile(lore_root)
    sf.load()
    sf.ingest_chain("ccat:data-center:computers", wiki="w")
    sf.ingest_chain("ccat:data-center:data-transfer", wiki="w")
    mapping = sf.rename("ccat:data-center", "ccat:infra")
    mapping_dict = dict(mapping)
    assert mapping_dict["ccat:data-center"] == "ccat:infra"
    assert mapping_dict["ccat:data-center:computers"] == "ccat:infra:computers"
    assert mapping_dict["ccat:data-center:data-transfer"] == "ccat:infra:data-transfer"
    assert set(sf.all_ids()) == {
        "ccat", "ccat:infra", "ccat:infra:computers", "ccat:infra:data-transfer"
    }


def test_rename_root_preserves_wiki(lore_root: Path) -> None:
    sf = ScopesFile(lore_root)
    sf.load()
    sf.ingest_chain("ccat:x", wiki="team-alpha")
    sf.rename("ccat", "ccat-fork")
    assert sf.get("ccat-fork").wiki == "team-alpha"


def test_rename_missing_raises(lore_root: Path) -> None:
    sf = ScopesFile(lore_root)
    sf.load()
    with pytest.raises(KeyError):
        sf.rename("nope", "anything")


# ---- reparent ----

def test_reparent_preserves_leaf(lore_root: Path) -> None:
    sf = ScopesFile(lore_root)
    sf.load()
    sf.ingest_chain("a:b:c", wiki="w")
    sf.ingest_chain("x", wiki="w2")
    sf.reparent("a:b", "x")
    assert sf.get("a:b") is None
    assert sf.get("x:b") is not None
    assert sf.get("x:b:c") is not None


# ---- remove ----

def test_remove_leaf(lore_root: Path) -> None:
    sf = ScopesFile(lore_root)
    sf.load()
    sf.ingest_chain("a:b", wiki="w")
    sf.remove("a:b")
    assert sf.get("a:b") is None
    assert sf.get("a") is not None   # root untouched


def test_remove_non_leaf_raises(lore_root: Path) -> None:
    sf = ScopesFile(lore_root)
    sf.load()
    sf.ingest_chain("a:b", wiki="w")
    with pytest.raises(ValueError):
        sf.remove("a")


def test_remove_missing_raises(lore_root: Path) -> None:
    sf = ScopesFile(lore_root)
    sf.load()
    with pytest.raises(KeyError):
        sf.remove("nope")
