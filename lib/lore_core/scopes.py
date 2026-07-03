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


def rewrite_scopes_in_frontmatter(
    wiki_root: Path,
    mapping: dict[str, str],
) -> int:
    """Rewrite ``scope:`` and ``scopes: [...]`` frontmatter values across
    every Markdown note under ``wiki_root``.

    Sibling primitive to ``state.attachments.rewrite_scopes``. Walks
    ``wiki_root.rglob("*.md")``, parses frontmatter, and applies
    ``mapping`` (old_id → new_id) to:

      - ``scope:`` (string field)
      - ``scopes:`` (list field)

    Subtree-aware: when an exact-match isn't in ``mapping``, also
    rewrites entries that *start with* a mapped prefix plus ``:``. This
    matches how attachments are rewritten — renaming ``ccat:data-center``
    cascades to ``ccat:data-center:ops-db``.

    Returns the number of files changed. Atomic per-file via
    :func:`lore_core.io.atomic_write_text`. Cross-file atomicity is
    *not* guaranteed; the caller orders writes.
    """
    from lore_core.io import atomic_write_text
    from lore_core.schema import parse_frontmatter, strip_frontmatter

    if not wiki_root.is_dir():
        return 0
    if not mapping:
        return 0

    def _apply(value: str) -> str:
        if value in mapping:
            return mapping[value]
        for old, new in mapping.items():
            prefix = old + ":"
            if value.startswith(prefix):
                return new + value[len(old) :]
        return value

    changed = 0
    for path in wiki_root.rglob("*.md"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        fm = parse_frontmatter(text)
        if not fm:
            continue

        new_fm: dict = dict(fm)
        dirty = False

        scope_val = fm.get("scope")
        if isinstance(scope_val, str) and scope_val:
            replacement = _apply(scope_val)
            if replacement != scope_val:
                new_fm["scope"] = replacement
                dirty = True

        scopes_val = fm.get("scopes")
        if isinstance(scopes_val, list):
            new_list = [_apply(s) if isinstance(s, str) else s for s in scopes_val]
            if new_list != scopes_val:
                new_fm["scopes"] = new_list
                dirty = True

        if not dirty:
            continue

        body = strip_frontmatter(text)
        try:
            import yaml

            dumped = yaml.safe_dump(new_fm, sort_keys=False, allow_unicode=True).strip()
        except Exception:  # noqa: BLE001 - never block on YAML edge cases
            continue
        new_text = f"---\n{dumped}\n---\n\n{body.lstrip()}"
        try:
            atomic_write_text(path, new_text)
            changed += 1
        except OSError:
            continue

    return changed
