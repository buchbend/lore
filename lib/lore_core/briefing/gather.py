"""Briefing gather — deterministic side of /lore:briefing.

Splits the briefing pipeline so the LLM only writes prose:

    deterministic gather (this module)  →  LLM compose body (skill)
                                       →  CLI publish (briefing.sinks)
                                       →  CLI mark-incorporated (ledger)

`gather()` is read-only: it returns the new sessions since the last
briefing plus the wiki's sink config + ledger state. The skill turns
that into prose, then shells out to `lore briefing publish` for the
sink-side write and `lore briefing mark` for the ledger commit.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from lore_core.config import get_wiki_root
from lore_core.errors import NO_VAULT, WIKI_NOT_FOUND, mcp_error
from lore_core.schema import parse_frontmatter

_LEDGER_FILE = ".briefing-ledger.json"
_CONFIG_FILE = ".lore-briefing.yml"

_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def _parse_session_path(
    rel_parts: tuple[str, ...], stem: str
) -> tuple[date, str] | None:
    """Extract (date, slug) from a session-note path.

    Supports two layouts:

    * Flat legacy::

          sessions/YYYY-MM-DD-slug.md
              → date from ``stem[:10]``, slug from ``stem[11:]``.

    * Sharded (team-mode and post-shard flat)::

          sessions/[<handle>/]YYYY/MM/DD-[HHMM-]slug.md
              → date from path parents + 2-digit ``DD`` prefix of stem;
                slug is whatever follows ``DD-`` (with optional 4-digit
                ``HHMM-`` segment dropped).

    Returns ``None`` for files that match neither shape (so callers can
    skip non-session noise like ``_recent.md``).
    """
    # Flat legacy first — date in stem[:10].
    try:
        d = date.fromisoformat(stem[:10])
        slug = stem[11:] if len(stem) > 11 else stem
        return d, slug
    except (ValueError, IndexError):
        pass

    # Sharded: need at least YYYY/MM/<file> in the path.
    if len(rel_parts) < 3:
        return None
    yyyy = rel_parts[-3]
    mm = rel_parts[-2]
    if not (yyyy.isdigit() and len(yyyy) == 4):
        return None
    if not (mm.isdigit() and len(mm) == 2):
        return None
    if len(stem) < 2 or not stem[:2].isdigit():
        return None
    try:
        d = date.fromisoformat(f"{yyyy}-{mm}-{stem[:2]}")
    except ValueError:
        return None

    # Strip leading "DD-" and optional "HHMM-".
    parts = stem.split("-", 2)
    if len(parts) >= 2 and parts[1].isdigit() and len(parts[1]) == 4:
        slug = parts[2] if len(parts) >= 3 else ""
    else:
        slug = "-".join(parts[1:])
    return d, slug or stem


def _read_ledger(wiki_path: Path) -> dict[str, Any]:
    path = wiki_path / _LEDGER_FILE
    if not path.exists():
        return {"last_briefing": None, "incorporated": []}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"last_briefing": None, "incorporated": []}


def _read_sink_config(wiki_path: Path) -> dict[str, Any] | None:
    path = wiki_path / _CONFIG_FILE
    if not path.exists():
        return None
    try:
        import yaml

        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        return None


def _extract_sections(text: str) -> dict[str, str]:
    """Map H2 heading → body text up to the next H2."""
    out: dict[str, str] = {}
    matches = list(_SECTION_RE.finditer(text))
    for i, m in enumerate(matches):
        title = m.group(1).strip().lower()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out[title] = text[body_start:body_end].strip()
    return out


def gather(
    *,
    wiki: str,
    since: str | None = None,
    include_body_sections: bool = True,
    user: str | None = None,
) -> dict[str, Any]:
    """Collect new session notes since the last briefing.

    Read-only — does NOT write the ledger. The caller composes the
    briefing prose, publishes via `lore briefing publish`, then commits
    the ledger update via `lore briefing mark`.

    Returns:
      {
        "wiki": <name>,
        "today": <YYYY-MM-DD>,
        "ledger": {"last_briefing": str|None, "incorporated_count": int},
        "sink_config": <dict|None>,
        "new_sessions": [
          {
            "path": str (relative to wiki),
            "date": str,
            "slug": str,
            "frontmatter": dict,
            "sections": {h2_title_lower: body_text}  (when include_body_sections)
          },
          ...
        ],
      }
    """
    wiki_root = get_wiki_root()
    if not wiki_root.exists():
        return mcp_error(
            NO_VAULT,
            f"no vault at {wiki_root}",
            next_="run `lore init` or set $LORE_ROOT",
        )
    wiki_path = wiki_root / wiki
    if not wiki_path.exists():
        return mcp_error(
            WIKI_NOT_FOUND,
            f"wiki not found: {wiki}",
            next_="run `lore status` to list configured wikis",
        )

    ledger = _read_ledger(wiki_path)
    incorporated = set(ledger.get("incorporated") or [])

    sessions_dir = wiki_path / "sessions"
    new_sessions: list[dict[str, Any]] = []
    if sessions_dir.is_dir():
        cutoff = date.fromisoformat(since) if since else None
        for md in sorted(sessions_dir.rglob("*.md")):
            stem = md.stem
            rel_parts = md.relative_to(sessions_dir).parts
            parsed = _parse_session_path(rel_parts, stem)
            if parsed is None:
                continue
            d, slug = parsed
            if cutoff and d < cutoff:
                continue
            rel = str(md.relative_to(wiki_path))
            # Match by stem (filename without extension) — robust against
            # sharded vs flat layouts.
            if md.name in incorporated or stem + ".md" in incorporated:
                continue
            text = md.read_text(errors="replace")
            entry: dict[str, Any] = {
                "path": rel,
                "date": d.isoformat(),
                "slug": slug or stem,
                "frontmatter": parse_frontmatter(text),
            }
            if user and entry["frontmatter"].get("user") != user:
                continue
            if include_body_sections:
                entry["sections"] = _extract_sections(text)
            new_sessions.append(entry)

    return {
        "wiki": wiki,
        "today": date.today().isoformat(),
        "ledger": {
            "last_briefing": ledger.get("last_briefing"),
            "incorporated_count": len(incorporated),
        },
        "sink_config": _read_sink_config(wiki_path),
        "new_sessions": new_sessions,
    }


def mark_incorporated(*, wiki: str, session_paths: list[str]) -> dict[str, Any]:
    """Append `session_paths` to the ledger's `incorporated` list.

    Caller is `lore briefing mark` — side-effecting (writes the ledger
    JSON; caller is responsible for the git commit).

    Each path may be a full relative path or just a filename — we store
    as filename for sharded-layout compatibility.
    """
    wiki_root = get_wiki_root()
    if not wiki_root.exists():
        return mcp_error(
            NO_VAULT,
            f"no vault at {wiki_root}",
            next_="run `lore init` or set $LORE_ROOT",
        )
    wiki_path = wiki_root / wiki
    if not wiki_path.exists():
        return mcp_error(
            WIKI_NOT_FOUND,
            f"wiki not found: {wiki}",
            next_="run `lore status` to list configured wikis",
        )

    ledger = _read_ledger(wiki_path)
    incorporated = list(ledger.get("incorporated") or [])
    added: list[str] = []
    for p in session_paths:
        name = Path(p).name
        if name not in incorporated:
            incorporated.append(name)
            added.append(name)

    ledger["incorporated"] = incorporated
    ledger["last_briefing"] = date.today().isoformat()

    ledger_path = wiki_path / _LEDGER_FILE
    ledger_path.write_text(json.dumps(ledger, indent=2) + "\n")
    return {
        "wiki": wiki,
        "ledger_path": str(ledger_path),
        "added": added,
        "incorporated_count": len(incorporated),
        "last_briefing": ledger["last_briefing"],
    }
