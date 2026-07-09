"""Bounded queries over the code map — the ``lore_codemap`` MCP tool's backend.

Consumers (planning skills) want ~30 relevant rows, never the full
``CODEMAP.md``. Three modes:

- ``symbols`` — symbols whose qualname matches a regex/substring pattern.
- ``directory`` — inventory rows scoped to a directory prefix (or the
  top-level bounded inventory when no prefix is given).
- ``top`` — the N highest-referenced symbols.

Each query builds (or reuses) a :class:`~lore_core.codemap.CodeMap` for the
repo root. Building is the expensive part (a full discovery + AST parse
pass), so results are cached in-process keyed on the root and the
generator's own fingerprint (git blob SHAs, or a content hash for non-git
trees — see :func:`lore_core.codemap.discover`). A cheap ``discover()`` call
checks whether the cached fingerprint is still current before deciding
whether to rebuild.

ponytail: cache lives in a module-level dict, one process lifetime (the MCP
server). No eviction — fine for the handful of repos one server touches in
a session; add an LRU cap if that stops being true.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from lore_core import codemap as cm

_cache: dict[str, tuple[str, cm.CodeMap]] = {}


def clear_cache() -> None:
    """Drop all cached maps. Test-only escape hatch."""
    _cache.clear()


def _get_code_map(root: Path) -> cm.CodeMap:
    """Return a fresh-enough :class:`CodeMap` for *root*, rebuilding on change.

    ``discover()`` alone (no AST parsing) is cheap enough to run on every
    query just to check the fingerprint; only a mismatch pays for a full
    :func:`build_code_map`.
    """
    root = Path(root).resolve()
    key = str(root)
    fingerprint = cm.discover(root).fingerprint
    cached = _cache.get(key)
    if cached is not None and cached[0] == fingerprint:
        return cached[1]
    code_map = cm.build_code_map(root)
    _cache[key] = (code_map.fingerprint, code_map)
    return code_map


def _symbol_to_dict(s: cm.Symbol) -> dict[str, Any]:
    return {
        "name": s.name,
        "qualname": s.qualname,
        "kind": s.kind,
        "path": s.relpath,
        "line": s.lineno,
        "refs": s.refs,
    }


def _dirstat_to_dict(d: cm.DirStat) -> dict[str, Any]:
    return {
        "path": d.path,
        "file_count": d.file_count,
        "total_bytes": d.total_bytes,
        "top_extensions": [{"ext": ext, "count": count} for ext, count in d.top_exts],
    }


def query_symbols(root: Path, pattern: str, limit: int = 30) -> dict[str, Any]:
    """Symbols whose qualname matches *pattern* (regex; falls back to a
    literal substring search if *pattern* isn't valid regex), ranked by refs,
    bounded to *limit*.
    """
    code_map = _get_code_map(root)
    try:
        matcher = re.compile(pattern)
        matches = [s for s in code_map.symbols if matcher.search(s.qualname)]
    except re.error:
        matches = [s for s in code_map.symbols if pattern in s.qualname]
    return {
        "mode": "symbols",
        "pattern": pattern,
        "symbols": [_symbol_to_dict(s) for s in matches[:limit]],
        "total_matches": len(matches),
    }


def query_directory(root: Path, directory: str | None, limit: int = 60) -> dict[str, Any]:
    """Inventory rows for *directory* and its subdirectories.

    ``directory=None`` (or empty) returns the map's top-level bounded
    inventory (already capped at ``MAX_DIR_ROWS`` by the generator).
    """
    code_map = _get_code_map(root)
    dirs = code_map.inventory.dirs
    if directory:
        prefix = directory.rstrip("/")
        dirs = [d for d in dirs if d.path == prefix or d.path.startswith(prefix + "/")]
    return {
        "mode": "directory",
        "directory": directory,
        "dirs": [_dirstat_to_dict(d) for d in dirs[:limit]],
    }


def query_top(root: Path, limit: int = 30) -> dict[str, Any]:
    """The top *limit* symbols, already ranked by refs in the code map."""
    code_map = _get_code_map(root)
    return {
        "mode": "top",
        "symbols": [_symbol_to_dict(s) for s in code_map.symbols[:limit]],
        "total_symbols": len(code_map.symbols),
    }
