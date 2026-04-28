"""Plan registry — read-only queries over the plan notes in a wiki.

Used by:

* SessionStart Resume block (``list_active`` for the per-repo banner).
* MCP ``lore_plan_active`` tool (same call, different transport).
* ``lore plan delete`` (``scan_incoming_wikilinks`` to refuse deletion
  when other notes reference the plan).

Critical design point: **always reads ``plans/*.md`` directly via glob
+ ``parse_frontmatter``**, never via ``_catalog.json``. The headline
demo path is ``accept → /clear → restart`` with no lint between, and
a stale catalog would silently kill the demo. Cheap (typical wiki
<50 plans).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date, datetime
from pathlib import Path
from typing import Any

from lore_core.schema import extract_wikilinks, parse_frontmatter

from .writer import plan_path, plans_dir

# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActivePlanCard:
    """One row in the SessionStart Resume block / lore_plan_active output."""

    slug: str
    path: Path
    description: str
    status: str
    repo: str | None
    step_status: dict[str, str]
    step_status_updated: str | None  # ISO timestamp string
    last_reviewed: str | None  # ISO date string
    step_ids: list[str]  # ordered list parsed from the body, for "X of N" + next-pending

    @property
    def steps_total(self) -> int:
        return len(self.step_ids)

    @property
    def steps_done(self) -> int:
        return sum(1 for sid in self.step_ids if self.step_status.get(sid) == "done")

    @property
    def steps_in_progress(self) -> list[str]:
        return [
            sid
            for sid in self.step_ids
            if self.step_status.get(sid) == "in_progress"
        ]

    @property
    def steps_blocked(self) -> list[str]:
        return [
            sid for sid in self.step_ids if self.step_status.get(sid) == "blocked"
        ]

    def next_pending_step(self) -> str | None:
        """First step with no entry in step_status — the natural "do this next" anchor."""
        for sid in self.step_ids:
            if sid not in self.step_status:
                return sid
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_active(
    wiki_root: Path,
    *,
    repo: str | None = None,
    include_wiki_general: bool = True,
) -> list[ActivePlanCard]:
    """Return ``status: active`` plans in the wiki, ranked by recency.

    Filtering rules:

    * If ``repo`` is provided, plans with matching ``repo:`` come first
      (sorted by ``last_reviewed`` desc), then wiki-general plans
      (those with no ``repo:``) if ``include_wiki_general``.
    * If ``repo`` is None, all active plans are returned, ranked by
      ``last_reviewed`` desc.

    The function is forgiving: malformed frontmatter, unparseable
    timestamps, and missing fields all skip silently rather than
    failing the SessionStart hot path.
    """
    pdir = plans_dir(wiki_root)
    if not pdir.exists():
        return []

    repo_matched: list[ActivePlanCard] = []
    wiki_general: list[ActivePlanCard] = []
    for path in sorted(pdir.glob("*.md")):
        card = _read_card(path)
        if card is None:
            continue
        if card.status != "active":
            continue
        if repo is not None and card.repo == repo:
            repo_matched.append(card)
        elif card.repo is None and include_wiki_general:
            wiki_general.append(card)
        elif repo is None:
            (repo_matched if card.repo else wiki_general).append(card)

    repo_matched.sort(key=_sort_key_recent, reverse=True)
    wiki_general.sort(key=_sort_key_recent, reverse=True)
    return repo_matched + wiki_general


def read_one(wiki_root: Path, slug: str) -> ActivePlanCard | None:
    """Read a single plan by slug. Returns None if absent or malformed."""
    return _read_card(plan_path(wiki_root, slug))


def scan_incoming_wikilinks(
    wiki_root: Path, slug: str, *, search_dirs: tuple[str, ...] = (
        "sessions", "concepts", "decisions", "projects",
    )
) -> list[Path]:
    """Return paths of notes that wikilink to ``plan/<slug>`` (with or without anchor).

    Used by ``lore plan delete`` to refuse deletion when other notes
    would be left with broken links. Walks the configured directories;
    wide enough to catch the common cases without scanning the whole
    wiki.
    """
    if not wiki_root.exists():
        return []
    matches: list[Path] = []
    target_prefix = f"plan/{slug}"
    for dirname in search_dirs:
        d = wiki_root / dirname
        if not d.exists():
            continue
        for path in d.rglob("*.md"):
            try:
                text = path.read_text()
            except OSError:
                continue
            for link in extract_wikilinks(text):
                # Match either bare ``plan/<slug>`` or ``plan/<slug>#sN``.
                if link == target_prefix or link.startswith(target_prefix + "#"):
                    matches.append(path)
                    break
    return matches


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _read_card(path: Path) -> ActivePlanCard | None:
    try:
        text = path.read_text()
    except OSError:
        return None
    fm = parse_frontmatter(text)
    if fm.get("type") != "plan":
        return None
    slug = fm.get("slug") or path.stem
    step_status = fm.get("step_status") or {}
    if not isinstance(step_status, dict):
        step_status = {}
    return ActivePlanCard(
        slug=str(slug),
        path=path,
        description=str(fm.get("description") or ""),
        status=str(fm.get("status") or "active"),
        repo=str(fm["repo"]) if fm.get("repo") else None,
        step_status={str(k): str(v) for k, v in step_status.items()},
        step_status_updated=_str_or_none(fm.get("step_status_updated")),
        last_reviewed=_str_or_none(fm.get("last_reviewed")),
        step_ids=_extract_step_ids_from_body(text),
    )


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime | _date):
        return value.isoformat()
    return str(value)


import re as _re

_STEP_HEADING_RE = _re.compile(r"^###\s+s(\d+)\b", _re.IGNORECASE)


def extract_step_ids_from_body(text: str) -> list[str]:
    """Parse ``### s<N>`` headings out of a plan note's body in document order.

    Public helper because ``step_status.set_step`` and Phase-4
    SessionStart rendering both need the canonical step-ID list. Used
    to be private + cross-module imported with an apologetic comment;
    promoted here so the seam is part of the contract.
    """
    from lore_core.schema import strip_frontmatter

    body = strip_frontmatter(text)
    ids: list[str] = []
    for line in body.split("\n"):
        m = _STEP_HEADING_RE.match(line)
        if m:
            ids.append(f"s{m.group(1)}")
    return ids


# Backward-compat alias — internal callers may still use the underscored name
# via existing imports.
_extract_step_ids_from_body = extract_step_ids_from_body


def _sort_key_recent(card: ActivePlanCard) -> str:
    """Sort key: most recently reviewed first.

    Empty / missing ``last_reviewed`` sorts as "least recent" so freshly
    captured plans (which always have today's date) outrank legacy
    notes without dates.
    """
    return card.last_reviewed or ""
