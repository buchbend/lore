"""One-shot backfill: rename session notes whose slug is cryptic.

Why this exists: until v0.41.x, the deterministic stub slug
(``_derive_slug`` in ``stub_note.py``) was set at first heartbeat and
never re-derived. When the first heartbeat carried no commits and a
generic ``files_touched`` (or none at all), the slug fell back to a
basename like ``attach`` / ``pyvenv`` / ``dockerfile`` or the time-based
``session-<scope>-<HHMM>``. Phase 2 synthesis later wrote a meaningful
title to frontmatter but never renamed the file — so the wikilink stem
stayed cryptic forever.

Phase 2 now renames forward; this pass walks the historical backlog
and applies the same rename retroactively. The old filename stem is
preserved as a frontmatter ``aliases:`` entry so existing
``[[old-stem]]`` references resolve via the wikilink-resolver's alias
lookup (see ``lore_core.wikilinks.existing_slugs``).

Skips:

- Notes still in ``state: stub`` (synthesis hasn't fired yet — those
  need the reaper, not us).
- Notes with ``part: 2+`` or a ``continues:`` field (renaming would
  orphan ``continued_by`` cross-references on the prior part).
- Notes whose title-derived slug already matches the filename slug
  (already healthy).
- Notes whose filename doesn't match the canonical
  ``<DD>-<HHMM>-<slug>.md`` shape.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import yaml

from lore_core.schema import parse_frontmatter
from lore_curator.session_filer import _slug


@dataclass
class BackfillPlan:
    old_path: Path
    new_path: Path
    title: str


@dataclass
class BackfillReport:
    scanned: int = 0
    skipped_stub: int = 0
    skipped_chain: int = 0
    skipped_no_title: int = 0
    skipped_already_canonical: int = 0
    skipped_malformed: int = 0
    planned: list[BackfillPlan] = field(default_factory=list)
    renamed: list[BackfillPlan] = field(default_factory=list)
    failed: list[tuple[Path, str]] = field(default_factory=list)


_STUB_DESC_PLACEHOLDER = "_synthesis pending_"


def plan_rename(path: Path) -> BackfillPlan | None:
    """Return a BackfillPlan if ``path`` should be renamed, else None.

    Pure: never touches disk beyond the read.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    fm = parse_frontmatter(text)
    if fm.get("state") == "stub":
        return None
    if fm.get("part") and int(fm.get("part") or 0) >= 2:
        return None
    if fm.get("continues"):
        return None
    title = (fm.get("title") or "").strip()
    if not title or title.startswith("session — ") or " session — " in title:
        # Empty or placeholder ``<scope> session — <date>`` — synthesis
        # never wrote a real title; nothing to rename to.
        return None
    desc = (fm.get("description") or "").strip()
    if desc == _STUB_DESC_PLACEHOLDER:
        return None
    new_slug = _slug(title)
    if not new_slug or new_slug == "session":
        return None
    parts = path.stem.split("-", 2)
    if len(parts) < 3:
        return None
    if not (parts[0].isdigit() and parts[1].isdigit() and len(parts[1]) == 4):
        # Not the ``<DD>-<HHMM>-<slug>.md`` shape.
        return None
    current_slug = parts[2]
    if current_slug == new_slug:
        return None
    prefix = f"{parts[0]}-{parts[1]}-"
    parent = path.parent
    candidate = parent / f"{prefix}{new_slug}.md"
    counter = 1
    while candidate.exists() and candidate != path:
        counter += 1
        candidate = parent / f"{prefix}{new_slug}-{counter}.md"
    return BackfillPlan(old_path=path, new_path=candidate, title=title)


def apply_rename(plan: BackfillPlan) -> None:
    """Rewrite ``plan.old_path`` with ``aliases: [old-stem]``, then
    ``os.replace`` it onto ``plan.new_path``.

    The alias is added so existing ``[[old-stem]]`` references keep
    resolving via the wikilink-resolver's alias lookup.
    """
    text = plan.old_path.read_text(encoding="utf-8")
    fm = parse_frontmatter(text)
    body = _strip_frontmatter(text)
    old_stem = plan.old_path.stem
    aliases = fm.get("aliases") or []
    if isinstance(aliases, str):
        aliases = [aliases]
    if old_stem not in aliases:
        aliases = [*aliases, old_stem]
    fm["aliases"] = aliases
    dumped = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    plan.old_path.write_text(f"---\n{dumped}\n---\n\n{body.lstrip()}", encoding="utf-8")
    os.replace(plan.old_path, plan.new_path)


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    after = text[end + 4 :]
    return after.lstrip("\n")


def iter_session_notes(wiki_path: Path) -> Iterable[Path]:
    sessions_dir = wiki_path / "sessions"
    if not sessions_dir.exists():
        return []
    return [
        p
        for p in sessions_dir.rglob("*.md")
        if not p.name.startswith("_") and ".processed" not in p.parts
    ]


def backfill_wiki(
    wiki_path: Path,
    *,
    apply: bool = False,
) -> BackfillReport:
    """Walk a wiki's session notes; plan + optionally apply renames."""
    report = BackfillReport()
    for path in iter_session_notes(wiki_path):
        report.scanned += 1
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        fm = parse_frontmatter(text)
        if fm.get("state") == "stub":
            report.skipped_stub += 1
            continue
        if (fm.get("part") and int(fm.get("part") or 0) >= 2) or fm.get("continues"):
            report.skipped_chain += 1
            continue
        plan = plan_rename(path)
        if plan is None:
            # plan_rename's None covers several reasons; classify the
            # most common ones from the frontmatter for the report.
            title = (fm.get("title") or "").strip()
            if not title or " session — " in title or title.startswith("session — "):
                report.skipped_no_title += 1
            else:
                report.skipped_already_canonical += 1
            continue
        report.planned.append(plan)
        if apply:
            try:
                apply_rename(plan)
                report.renamed.append(plan)
            except OSError as exc:
                report.failed.append((path, str(exc)))
    return report
