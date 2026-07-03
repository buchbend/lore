"""Repo-native pull for ratified decisions — ADRs and PRDs.

Reads directly from a connected repo's conventional, hard-coded homes
(``docs/adr/``, ``docs/prd/``) instead of extracting decisions from
session transcripts: repos own ratified decisions, lore owns session
history (PRD 0001). Configurable homes are explicitly out of scope
until adoption warrants it.

Pure filesystem logic; the MCP layer (``lore_mcp.server``) wraps this
in the pull-only tool handlers and never wires it into ambient
context.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lore_core.schema import parse_frontmatter

HOMES: dict[str, str] = {"adr": "docs/adr", "prd": "docs/prd"}

_INDEX_NAMES = {"index.md", "readme.md"}


def home_dir(repo_root: Path, kind: str) -> Path:
    """Return the conventional home directory for `kind` under `repo_root`."""
    return repo_root / HOMES[kind]


def _title_from_body(text: str) -> str | None:
    """First H1 in document order, or None if the doc has none."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            return stripped[2:].strip()
    return None


def _entry_for(path: Path, repo_root: Path) -> dict[str, Any]:
    text = path.read_text(errors="replace")
    fm = parse_frontmatter(text)
    title = fm.get("title") or _title_from_body(text) or path.stem
    return {
        "path": str(path.relative_to(repo_root)),
        "title": title,
        "status": fm.get("status"),
        "is_index": path.name.lower() in _INDEX_NAMES,
    }


def list_docs(repo_root: Path, kind: str) -> list[dict[str, Any]]:
    """List ADR/PRD entries under `kind`'s home dir.

    Empty list (not an error) when the home dir doesn't exist — most
    repos have no ADRs yet, or don't use PRDs at all. Index files
    (``README.md``/``index.md``) are included and sorted first.
    """
    d = home_dir(repo_root, kind)
    if not d.is_dir():
        return []
    entries = [_entry_for(p, repo_root) for p in sorted(d.glob("*.md"))]
    entries.sort(key=lambda e: (not e["is_index"], e["path"]))
    return entries


def resolve_doc(repo_root: Path, kind: str, path: str) -> Path | None:
    """Resolve `path` to an absolute file under `kind`'s home dir.

    Accepts a bare slug (``0001-x``), a filename (``0001-x.md``), or
    the full repo-relative path (``docs/adr/0001-x.md``). Returns None
    if the resolved file escapes the home dir, doesn't exist, or isn't
    a file — callers don't need to distinguish "not found" from "path
    escape", so both collapse to the same not-found result.
    """
    d = home_dir(repo_root, kind)
    home = HOMES[kind]
    p = path.strip()
    if p.startswith(f"{home}/"):
        p = p[len(home) + 1 :]
    if not p.endswith(".md"):
        p = f"{p}.md"
    target = (d / p).resolve()
    try:
        target.relative_to(d.resolve())
    except ValueError:
        return None
    if not target.is_file():
        return None
    return target


def read_doc(repo_root: Path, kind: str, path: str) -> dict[str, Any] | None:
    """Read one ADR/PRD file. None if not found, not a file, or escapes the home dir."""
    target = resolve_doc(repo_root, kind, path)
    if target is None:
        return None
    text = target.read_text(errors="replace")
    fm = parse_frontmatter(text)
    title = fm.get("title") or _title_from_body(text) or target.stem
    return {
        "path": str(target.relative_to(repo_root)),
        "title": title,
        "status": fm.get("status"),
        "content": text,
    }
