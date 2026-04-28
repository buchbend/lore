"""Journal — freeform side-chains for the AI and human collaborators.

Two newest-first markdown logs at vault top-level:

  $LORE_ROOT/journals/ai.md      — the model writes whatever it likes
  $LORE_ROOT/journals/human.md   — the user's scratch pad

Distinct from the ``journal`` *surface* (auto-extracted by Curator B
into the wiki). The journals here are the *non-derived* channels:
jokes, criticism, half-formed ideas, weather, anything that wouldn't
survive auto-extraction. The bar to write must be near-zero, so this
module deliberately avoids frontmatter, schema validation, or
anything else that would turn a journal entry into work.

Feature-flagged via ``journal.enabled`` in ``$LORE_ROOT/.lore/config.yml``.
Default off — opt-in. The flag controls SessionStart injection and
MCP tool dispatch; CLI ``lore journal write/read`` works regardless
because a human writing to their own scratch pad shouldn't need a
toggle to be on.
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import yaml

from lore_core.config import get_lore_root
from lore_core.root_config import load_root_config

JournalKind = Literal["ai", "human"]
VALID_KINDS: tuple[str, ...] = ("ai", "human")

_HEADERS: dict[str, str] = {
    "ai": (
        "# AI Journal\n\n"
        "Freeform entries from the model — observations, criticism, "
        "workflow ideas, inefficiencies, jokes, weather. Newest at "
        "the top. Not extracted; not curated; not derived. The bar "
        "is *would this be lost otherwise*, not *does this serve "
        "the user*.\n\n"
    ),
    "human": (
        "# Human Journal\n\n"
        "Freeform entries from you — things curators wouldn't catch "
        "automatically. Newest at the top. Plain markdown — edit "
        "directly any time.\n\n"
    ),
}

_TIMESTAMP_FMT = "%Y-%m-%dT%H:%M"
_SLUG_RE = re.compile(r"[^a-z0-9-]+")
_ENTRY_HEAD = re.compile(r"^## (\S+) — (\S+)\s*$")


def _check_kind(kind: str) -> None:
    if kind not in VALID_KINDS:
        raise ValueError(
            f"journal kind must be one of {VALID_KINDS!r}, got {kind!r}"
        )


def journal_path(kind: JournalKind, lore_root: Path | None = None) -> Path:
    """Return the on-disk path for a journal file."""
    _check_kind(kind)
    root = lore_root or get_lore_root()
    return root / "journals" / f"{kind}.md"


def enabled(lore_root: Path | None = None) -> bool:
    """True iff ``journal.enabled`` is set in the root config."""
    root = lore_root or get_lore_root()
    cfg = load_root_config(root)
    return bool(getattr(getattr(cfg, "journal", None), "enabled", False))


# ---------------------------------------------------------------------------
# Author resolution
# ---------------------------------------------------------------------------


def _slug(value: str) -> str:
    """Lowercase, alphanumeric-and-dash slug for author tags."""
    value = value.strip().lower()
    value = re.sub(r"\s+", "-", value)
    value = _SLUG_RE.sub("", value)
    return value or "anon"


def _git_config(key: str) -> str:
    try:
        result = subprocess.run(
            ["git", "config", "--get", key],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def default_author(kind: JournalKind, lore_root: Path | None = None) -> str:
    """Resolve a default author tag for an entry.

    AI side: ``$LORE_AI_AUTHOR`` → ``$CLAUDE_MODEL_ID`` → ``"claude"``.
    Human side: ``$LORE_USER_HANDLE`` → ``git config user.name``
    (slugged) → ``git config user.email`` local-part → ``"human"``.
    """
    _check_kind(kind)
    if kind == "ai":
        for var in ("LORE_AI_AUTHOR", "CLAUDE_MODEL_ID"):
            value = os.environ.get(var, "").strip()
            if value:
                return _slug(value)
        return "claude"

    handle = os.environ.get("LORE_USER_HANDLE", "").strip()
    if handle:
        return _slug(handle)
    name = _git_config("user.name")
    if name:
        return _slug(name)
    email = _git_config("user.email")
    if email and "@" in email:
        return _slug(email.split("@", 1)[0])
    return "human"


# ---------------------------------------------------------------------------
# Read / write
# ---------------------------------------------------------------------------


def _ensure_file(path: Path, kind: JournalKind) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_HEADERS[kind], encoding="utf-8")


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime(_TIMESTAMP_FMT)


def write(
    kind: JournalKind,
    text: str,
    *,
    author: str | None = None,
    lore_root: Path | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Prepend a new entry to the journal. Returns metadata about the write.

    The newest entry sits directly under the file header (or at the
    top of the file when the header was hand-edited away). Existing
    entries are never mutated.
    """
    _check_kind(kind)
    text = text.strip()
    if not text:
        raise ValueError("journal entry text must be non-empty")
    root = lore_root or get_lore_root()
    path = journal_path(kind, root)
    _ensure_file(path, kind)
    author_tag = _slug(author) if author else default_author(kind, root)
    stamp = timestamp or _now_stamp()
    entry = f"## {stamp} — {author_tag}\n{text}\n\n"
    existing = path.read_text(encoding="utf-8")
    header = _HEADERS[kind]
    if existing.startswith(header):
        body = existing[len(header):]
    else:
        body = existing
        header = ""
    path.write_text(header + entry + body, encoding="utf-8")
    return {
        "kind": kind,
        "path": str(path),
        "author": author_tag,
        "timestamp": stamp,
        "bytes": len(entry),
    }


def read(
    kind: JournalKind,
    *,
    limit: int | None = None,
    lore_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Return parsed entries, newest-first.

    Each entry: ``{"timestamp", "author", "body"}``. Lines that don't
    follow the ``## YYYY-... — author`` head become the body of the
    entry above them; orphan content before any head is dropped.
    """
    _check_kind(kind)
    root = lore_root or get_lore_root()
    path = journal_path(kind, root)
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    header = _HEADERS[kind]
    if text.startswith(header):
        text = text[len(header):]
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    body_lines: list[str] = []
    for line in text.splitlines():
        m = _ENTRY_HEAD.match(line)
        if m:
            if current is not None:
                current["body"] = "\n".join(body_lines).strip()
                entries.append(current)
            current = {"timestamp": m.group(1), "author": m.group(2)}
            body_lines = []
        elif current is not None:
            body_lines.append(line)
    if current is not None:
        current["body"] = "\n".join(body_lines).strip()
        entries.append(current)
    if limit is not None:
        return entries[:limit]
    return entries


# ---------------------------------------------------------------------------
# Feature flag toggle (writes to $LORE_ROOT/.lore/config.yml)
# ---------------------------------------------------------------------------


def set_enabled(value: bool, lore_root: Path | None = None) -> Path:
    """Persist ``journal.enabled = value`` to ``$LORE_ROOT/.lore/config.yml``.

    Preserves existing keys (observability, curator, …). Returns the
    path written.
    """
    root = lore_root or get_lore_root()
    path = root / ".lore" / "config.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    raw: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError:
            loaded = {}
        if isinstance(loaded, dict):
            raw = loaded
    section = raw.get("journal")
    if not isinstance(section, dict):
        section = {}
        raw["journal"] = section
    section["enabled"] = bool(value)
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path
