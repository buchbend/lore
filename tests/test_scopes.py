"""Tests for lore_core.scopes — the shared scope-tree helpers."""

from __future__ import annotations

from lore_core.scopes import (
    load_scopes_yml,
    subtree_members,
    subtree_siblings,
    walk_scope_leaves,
)

TREE = {
    "scopes": {
        "ccat": {
            "children": {
                "data-center": {
                    "children": {
                        "data-transfer": {"repo": "ccatobs/data-transfer"},
                        "system-integration": {"repo": "ccatobs/system-integration"},
                    },
                },
                "instrument": {
                    "children": {
                        "atm-calibration": {"repo": "ccatobs/atm-calibration"},
                    },
                },
            },
        },
    }
}


def test_walk_unwraps_scopes_key():
    leaves = sorted(walk_scope_leaves(TREE))
    assert ("ccat:data-center:data-transfer", "ccatobs/data-transfer") in leaves
    assert ("ccat:instrument:atm-calibration", "ccatobs/atm-calibration") in leaves


def test_walk_accepts_bare_tree():
    # Pass the inner dict directly (no top-level `scopes:` key)
    leaves = sorted(walk_scope_leaves(TREE["scopes"]))
    assert len(leaves) == 3


def test_subtree_siblings_parent_level():
    sibs = subtree_siblings(TREE, "ccat:data-center:data-transfer")
    repos = {r for _, r in sibs}
    assert repos == {"ccatobs/system-integration"}


def test_subtree_members_inclusive():
    """subtree_members returns every descendant including itself at the prefix."""
    members = subtree_members(TREE, "ccat:data-center")
    repos = {r for _, r in members}
    assert repos == {"ccatobs/data-transfer", "ccatobs/system-integration"}


def test_subtree_members_exact_leaf():
    members = subtree_members(TREE, "ccat:data-center:data-transfer")
    assert [(s, r) for s, r in members] == [
        ("ccat:data-center:data-transfer", "ccatobs/data-transfer")
    ]


def test_subtree_members_whole_tree():
    members = subtree_members(TREE, "ccat")
    repos = {r for _, r in members}
    assert repos == {
        "ccatobs/data-transfer",
        "ccatobs/system-integration",
        "ccatobs/atm-calibration",
    }


def test_subtree_members_unknown_prefix():
    assert subtree_members(TREE, "nope") == []


def test_load_scopes_yml(tmp_path):
    (tmp_path / "_scopes.yml").write_text(
        "scopes:\n  foo:\n    children:\n      bar:\n        repo: org/bar\n"
    )
    data = load_scopes_yml(tmp_path)
    leaves = list(walk_scope_leaves(data))
    assert leaves == [("foo:bar", "org/bar")]


def test_load_scopes_yml_missing(tmp_path):
    assert load_scopes_yml(tmp_path) == {}
