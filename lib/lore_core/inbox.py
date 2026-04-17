"""Inbox classifier — deterministic side of /lore:inbox.

`classify()` walks every inbox in the vault and returns a structured
list of files with detected type + routing hint. The skill then reads
each file (LLM judgment), composes vault notes, and runs `lore inbox
archive` to move the source to `.processed/`.

Inbox locations:
  - $LORE_ROOT/inbox/                     (root triage)
  - $LORE_ROOT/wiki/<name>/inbox/         (per-wiki, pre-routed)
"""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path
from typing import Any

from lore_core.config import get_wiki_root

_TYPE_BY_EXT = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
    ".pdf": "pdf",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".gif": "image",
    ".ipynb": "notebook",
    ".rst": "rst",
    ".py": "code",
    ".rs": "code",
    ".js": "code",
    ".ts": "code",
    ".go": "code",
    ".java": "code",
    ".c": "code",
    ".cpp": "code",
    ".h": "code",
    ".sh": "code",
    ".toml": "config",
    ".yml": "config",
    ".yaml": "config",
    ".json": "config",
}


def _classify_one(path: Path, target_wiki: str | None) -> dict[str, Any]:
    ext = path.suffix.lower()
    ftype = _TYPE_BY_EXT.get(ext, "unknown")
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return {
        "path": str(path),
        "filename": path.name,
        "extension": ext,
        "type": ftype,
        "size_bytes": size,
        "target_wiki": target_wiki,
        "needs_triage": target_wiki is None,
    }


def classify(*, vault_root: Path | None = None) -> dict[str, Any]:
    """Walk every inbox in the vault and classify what's in there.

    Read-only. Skips `.processed/` dirs and hidden files.
    """
    if vault_root is None:
        wiki_root = get_wiki_root()
        if not wiki_root.exists():
            return {"error": f"No vault at {wiki_root}"}
        vault_root = wiki_root.parent
    vault_root = vault_root.resolve()

    files: list[dict[str, Any]] = []

    # 1. Root inbox — needs triage
    root_inbox = vault_root / "inbox"
    if root_inbox.is_dir():
        for entry in sorted(root_inbox.iterdir()):
            if entry.is_dir() or entry.name.startswith("."):
                continue
            files.append(_classify_one(entry, target_wiki=None))

    # 2. Per-wiki inboxes — pre-routed
    wiki_root = vault_root / "wiki"
    if wiki_root.is_dir():
        for wiki_dir in sorted(wiki_root.iterdir()):
            if not wiki_dir.is_dir() or wiki_dir.name.startswith("."):
                continue
            inbox = wiki_dir / "inbox"
            if not inbox.is_dir():
                continue
            for entry in sorted(inbox.iterdir()):
                if entry.is_dir() or entry.name.startswith("."):
                    continue
                files.append(_classify_one(entry, target_wiki=wiki_dir.name))

    by_inbox: dict[str, list[str]] = {}
    by_type: dict[str, int] = {}
    for f in files:
        loc = f["target_wiki"] or "(root)"
        by_inbox.setdefault(loc, []).append(f["filename"])
        by_type[f["type"]] = by_type.get(f["type"], 0) + 1

    return {
        "vault_root": str(vault_root),
        "files": files,
        "by_inbox": by_inbox,
        "by_type": by_type,
        "total": len(files),
    }


def archive(*, source: Path, processed_dir: Path | None = None) -> dict[str, Any]:
    """Move a source inbox file to `.processed/<YYYY-MM-DD>_<name>`.

    Side-effecting — exposed via `lore inbox archive`. Returns metadata
    about the move (or an error). Date-prefixes the name to keep order
    visible in `ls`.
    """
    source = source.resolve()
    if not source.exists():
        return {"error": f"Source not found: {source}"}
    if not source.is_file():
        return {"error": f"Not a file: {source}"}
    target_dir = processed_dir or (source.parent / ".processed")
    target_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    target = target_dir / f"{today}_{source.name}"
    # Don't clobber an existing archive
    if target.exists():
        i = 1
        while True:
            candidate = target_dir / f"{today}_{i:02d}_{source.name}"
            if not candidate.exists():
                target = candidate
                break
            i += 1
    shutil.move(str(source), str(target))
    return {
        "source": str(source),
        "archived_to": str(target),
    }
