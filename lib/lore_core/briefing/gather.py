"""Briefing gather — deterministic side of /lore:briefing.

PRD 0011 parks the briefing feature rather than reviving it. Nothing
writes a session note since the compose pipeline was retired, so
`gather()` no longer walks `<wiki>/sessions/` — `new_sessions` is
always empty. The ledger and sink-config reads stay: `lore briefing
publish` and `lore briefing mark` still read this shape.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from lore_core.config import get_wiki_root
from lore_core.errors import NO_VAULT, WIKI_NOT_FOUND, mcp_error

_LEDGER_FILE = ".briefing-ledger.json"
_CONFIG_FILE = ".lore-briefing.yml"


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


def gather(
    *,
    wiki: str,
    since: str | None = None,
    include_body_sections: bool = True,
    user: str | None = None,
    epic: int | None = None,
) -> dict[str, Any]:
    """Report the wiki's briefing ledger and sink config.

    Read-only. `since`, `include_body_sections`, `user` and `epic` filtered
    the `<wiki>/sessions/` walk this function used to do; the walk is gone
    (PRD 0013) and `new_sessions` is always empty, so the parameters are
    kept only so `lore briefing publish` and `lore briefing mark` need no
    call-site change.

    Returns:
      {
        "wiki": <name>,
        "today": <YYYY-MM-DD>,
        "ledger": {"last_briefing": str|None, "incorporated_count": int},
        "sink_config": <dict|None>,
        "new_sessions": [],
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

    return {
        "wiki": wiki,
        "today": date.today().isoformat(),
        "ledger": {
            "last_briefing": ledger.get("last_briefing"),
            "incorporated_count": len(incorporated),
        },
        "sink_config": _read_sink_config(wiki_path),
        "new_sessions": [],
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
