"""Scope tree helpers — read `_scopes.yml`, walk the tree, find subtrees.

The `scope:` field is colon-separated and hierarchical
(`ccat:data-center:data-transfer`). Each wiki may carry a `_scopes.yml`
at its root that declares which repos live under which scope path.
See concepts/lore/scopes-hierarchical in the design vault.

All functions in this module are pure — no I/O beyond reading the yaml
file, no subprocess calls. Intended to be called from both hooks and
CLI subcommands.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path


def load_scopes_yml(wiki_path: Path) -> dict:
    """Load `_scopes.yml` from a wiki root. Returns {} on missing/malformed."""
    path = wiki_path / "_scopes.yml"
    if not path.exists():
        return {}
    try:
        import yaml

        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        return {}


def walk_scope_leaves(tree: dict, prefix: list[str] | None = None) -> Iterator[tuple[str, str]]:
    """Yield (scope_path, repo_slug) for every leaf with a `repo:` field.

    Accepts either the top-level dict (with `scopes:` key) or the bare
    scope tree. Traverses `children:` recursively.
    """
    if not isinstance(tree, dict):
        return
    # Accept a top-level {"scopes": ...} wrapper by unwrapping once.
    if prefix is None and "scopes" in tree and isinstance(tree["scopes"], dict):
        tree = tree["scopes"]
    if prefix is None:
        prefix = []
    for key, value in tree.items():
        if not isinstance(value, dict):
            continue
        path = prefix + [key]
        repo = value.get("repo")
        if repo:
            yield ":".join(path), repo
        children = value.get("children")
        if children:
            yield from walk_scope_leaves(children, path)


def subtree_siblings(
    scopes_yml: dict,
    current_scope: str,
) -> list[tuple[str, str]]:
    """Return repos (scope_path, repo_slug) in the parent subtree.

    Excludes `current_scope` itself. Returns [] if the scope has no
    parent (top-level) or the tree is empty.
    """
    parts = current_scope.split(":")
    if len(parts) < 2:
        return []
    parent_prefix = ":".join(parts[:-1])
    out: list[tuple[str, str]] = []
    for path, repo in walk_scope_leaves(scopes_yml):
        if path == current_scope:
            continue
        if path.startswith(parent_prefix + ":") or path == parent_prefix:
            out.append((path, repo))
    return out


def subtree_members(
    scopes_yml: dict,
    scope_prefix: str,
) -> list[tuple[str, str]]:
    """Return every (scope_path, repo_slug) under a scope prefix.

    Unlike `subtree_siblings`, this is inclusive: passing the exact
    scope of a leaf returns that leaf. Passing a higher-level prefix
    returns all descendant leaves.
    """
    out: list[tuple[str, str]] = []
    for path, repo in walk_scope_leaves(scopes_yml):
        if path == scope_prefix or path.startswith(scope_prefix + ":"):
            out.append((path, repo))
    return out
