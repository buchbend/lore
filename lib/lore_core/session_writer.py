"""Shared session-note writer — the single place a session note is filed.

Both the explicit `/lore:session` path (via `lore_core/session.py`) and
the passive curator-A path (via `lore_curator/session_filer.py`) funnel
into `file_or_merge` here. Differences between the two flows — who
composes the body, whether transcript provenance is present, whether an
LLM wrote anything — are captured in the `SessionInput` dataclass;
everything else (path, append-to-today merge rule, frontmatter render,
atomic write) lives here.

Layout invariant
----------------

Every session note lives at::

    <wiki>/sessions[/<handle>]/<YYYY>/<MM>/<DD>-<slug>.md

``<handle>`` is present iff the wiki is in team mode (``_users.yml``
exists — see `lore_core.identity.session_note_dir`). Append-to-today
searches the *handle-scoped* month directory so concurrent authors
don't collide.

Transcripts cap
---------------

Session notes carry a ``transcripts:`` list of source UUIDs for passive
capture. The list is capped at 20 most-recent UUIDs. See
`lore_curator/session_filer.py` docstring for the full provenance
contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date as _date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from lore_core.identity import session_note_dir
from lore_core.io import atomic_write_text
from lore_core.schema import parse_frontmatter
from lore_core.types import Scope, TranscriptHandle

if TYPE_CHECKING:
    from lore_core.run_log import RunLogger


_TRANSCRIPTS_CAP = 20


# Topic-signal-vs-boilerplate classification moved to lore_core.topic_files
# in v0.8.2 once two consumers needed it. Re-exported here under the
# previous private names for backward compat with any external callers.
from lore_core.topic_files import (
    BOILERPLATE_FILES as _TOPIC_BOILERPLATE_FILES,  # noqa: F401
    strip_boilerplate as _strip_boilerplate,
)

# Jaccard threshold above which two file-sets are "the same topic" and
# the new chunk merges into the open note. Hand-picked: 0.3 means the
# overlap covers about a third of the union — enough to indicate
# continuation, low enough to avoid spurious links from e.g. one shared
# helper module. Tunable later via root config if calibration shows
# false-positives or false-negatives.
_TOPIC_OVERLAP_MIN_JACCARD = 0.3


def _dedup_preserving_order(items: list[str] | None) -> list[str]:
    """First-seen order; truthy strings only, non-string entries dropped.

    Used by frontmatter writers so ``files_touched`` / ``transcripts``
    style fields produce stable, readable diffs across appends.
    """
    if not items:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if isinstance(item, str) and item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _topic_jaccard(a: list[str] | None, b: list[str] | None) -> float:
    """Jaccard similarity between two file-set lists, ignoring boilerplate.

    Returns 0.0 for either side empty (no signal). Returns 1.0 if both
    sides are equal non-empty sets.
    """
    sa = _strip_boilerplate(a)
    sb = _strip_boilerplate(b)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


@dataclass
class SessionInput:
    """Inputs common to both the explicit and passive session flows.

    Required for every call::

        scope, wiki_root, work_time, handle, slug, description, body_markdown

    ``handle`` may be empty string — in solo mode that's fine; in team
    mode, an empty handle will skip sharding and the note will live in
    the flat ``sessions/YYYY/MM/…`` directory (caller's responsibility
    to pass the right handle).
    """

    scope: Scope
    wiki_root: Path
    work_time: datetime
    handle: str
    slug: str
    description: str
    body_markdown: str
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    summary: str = ""
    tags: list[str] = field(default_factory=list)
    extra_frontmatter: dict[str, Any] = field(default_factory=dict)

    # Passive-capture provenance (omit for explicit writes).
    transcript: TranscriptHandle | None = None
    turn_hashes: tuple[str | None, str | None] | None = None
    scope_redirected_from: str | None = None

    # Phase C: file paths actually touched by this chunk's tool calls.
    # Persisted in frontmatter so future chunks can decide topic-merge
    # via file-set overlap. Already-stripped of boilerplate before being
    # handed in is fine but not required — the merge logic strips again.
    files_touched: list[str] = field(default_factory=list)


@dataclass
class FiledNote:
    path: Path
    wikilink: str               # e.g. "[[19-add-ledger]]"
    was_merge: bool             # True if appended to an existing note


def file_or_merge(
    si: SessionInput,
    *,
    logger: "RunLogger | None" = None,
    transcript_id: str | None = None,
) -> FiledNote:
    """Create a new session note or append to today's open note.

    ``transcript_id`` is only used for logging when provided — it can
    differ from ``si.transcript.id`` in tests.
    """
    sessions_base = session_note_dir(si.wiki_root, si.handle)
    month_dir = _month_dir(sessions_base, si.work_time)
    month_dir.mkdir(parents=True, exist_ok=True)

    today_note = _find_todays_open_note(
        sessions_base, scope=si.scope, work_date=si.work_time.date(),
        new_files_touched=si.files_touched,
    )
    if today_note is not None:
        if logger is not None:
            logger.emit(
                "merge-check",
                transcript_id=transcript_id,
                target=f"[[{today_note.stem}]]",
                similarity=None,
                decision="append-today",
            )
        _append_to_note(today_note, si)
        wikilink = f"[[{today_note.stem}]]"
        if logger is not None:
            logger.emit(
                "session-note",
                transcript_id=transcript_id,
                action="merged",
                wikilink=wikilink,
            )
        return FiledNote(path=today_note, wikilink=wikilink, was_merge=True)

    day_prefix = f"{si.work_time.day:02d}"
    path = month_dir / f"{day_prefix}-{si.slug}.md"
    counter = 1
    while path.exists():
        counter += 1
        path = month_dir / f"{day_prefix}-{si.slug}-{counter}.md"

    _write_new_note(path, si)
    wikilink = f"[[{path.stem}]]"
    if logger is not None:
        logger.emit(
            "session-note",
            transcript_id=transcript_id,
            action="filed",
            path=str(path),
            wikilink=wikilink,
        )
    return FiledNote(path=path, wikilink=wikilink, was_merge=False)


# ---- private helpers --------------------------------------------------------


def _month_dir(sessions_base: Path, work_time: datetime) -> Path:
    return sessions_base / str(work_time.year) / f"{work_time.month:02d}"


def _find_todays_open_note(
    sessions_base: Path,
    *,
    scope: Scope,
    work_date: _date,
    new_files_touched: list[str] | None = None,
) -> Path | None:
    """Find a same-day same-scope open note that the new chunk should
    merge into.

    Phase C: when ``new_files_touched`` is given AND any candidate has
    its own ``files_touched`` frontmatter, require a Jaccard overlap of
    at least :data:`_TOPIC_OVERLAP_MIN_JACCARD` (boilerplate-stripped)
    so disjoint topics on the same day end up in different notes.

    Backward compat: if either side has no ``files_touched`` info
    (legacy notes pre-Phase-C, or talk-only chunks with no tool calls),
    fall through to the pre-Phase-C "most recent same-day same-scope
    note" rule. This preserves existing behaviour for anything filed
    before the upgrade.
    """
    month = sessions_base / str(work_date.year) / f"{work_date.month:02d}"
    if not month.exists():
        return None
    day_prefix = f"{work_date.day:02d}"
    new_set = _strip_boilerplate(new_files_touched)

    # (-jaccard, -mtime, path) — best topic match first; ties break on
    # most-recent. Negative values let us sort ascending.
    candidates: list[tuple[float, float, Path]] = []
    for p in month.glob(f"{day_prefix}-*.md"):
        try:
            text = p.read_text()
        except OSError:
            continue
        fm = parse_frontmatter(text)
        if fm.get("scope") != scope.scope:
            continue
        if fm.get("closed"):
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue

        candidate_files = fm.get("files_touched") or []
        candidate_set = _strip_boilerplate(
            candidate_files if isinstance(candidate_files, list) else []
        )

        # Decision rules:
        # - Both sides have non-empty file sets → require Jaccard ≥ threshold.
        # - New chunk has files but candidate has none (legacy / talk-only):
        #   refuse to merge. On the upgrade day, a single legacy note
        #   would otherwise become an attractor for every new file-bearing
        #   chunk regardless of topic — exactly the Frankenstein note we
        #   want to avoid.
        # - New chunk is talk-only: no signal to discriminate topics, so
        #   fall back to pre-Phase-C "most-recent same-day same-scope"
        #   matching. Continuing-the-conversation case.
        if new_set and candidate_set:
            jac = len(new_set & candidate_set) / len(new_set | candidate_set)
            if jac < _TOPIC_OVERLAP_MIN_JACCARD:
                continue
            candidates.append((-jac, -mtime, p))
        elif new_set and not candidate_set:
            # Skip — don't attract file-bearing chunks into ambiguous legacy.
            continue
        else:
            candidates.append((0.0, -mtime, p))

    if not candidates:
        return None
    candidates.sort()
    return candidates[0][2]


def _build_frontmatter(si: SessionInput) -> dict[str, Any]:
    from_hash = si.turn_hashes[0] if si.turn_hashes else None
    to_hash = si.turn_hashes[1] if si.turn_hashes else None

    fm: dict[str, Any] = {
        "schema_version": 2,
        "type": "session",
        "created": si.work_time.date().isoformat(),
        "last_reviewed": si.work_time.date().isoformat(),
        "description": si.description,
    }
    if si.summary:
        fm["summary"] = si.summary
    fm["scope"] = si.scope.scope
    if si.handle:
        fm["user"] = si.handle
    if si.transcript is not None:
        fm["draft"] = True
        fm["curator_a_run"] = si.now.isoformat()
        fm["source_transcripts"] = [
            {
                "integration": si.transcript.integration,
                "id": si.transcript.id,
                "from_hash": from_hash,
                "to_hash": to_hash,
            }
        ]
        fm["transcripts"] = [si.transcript.id]
    if si.tags:
        fm["tags"] = si.tags
    if si.files_touched:
        fm["files_touched"] = _dedup_preserving_order(si.files_touched)
    for k, v in si.extra_frontmatter.items():
        fm.setdefault(k, v)
    if si.scope_redirected_from:
        fm["scope_redirected_from"] = si.scope_redirected_from
    return fm


def _write_new_note(path: Path, si: SessionInput) -> None:
    fm = _build_frontmatter(si)
    text = _render_markdown(fm, si.body_markdown)
    atomic_write_text(path, text)


def _append_to_note(path: Path, si: SessionInput) -> None:
    text = path.read_text()
    fm = parse_frontmatter(text)
    body = _strip_frontmatter(text)

    fm["last_reviewed"] = si.work_time.date().isoformat()

    if si.transcript is not None:
        from_hash = si.turn_hashes[0] if si.turn_hashes else None
        to_hash = si.turn_hashes[1] if si.turn_hashes else None

        fm["curator_a_run"] = si.now.isoformat()
        src = fm.get("source_transcripts") or []
        src.append(
            {
                "integration": si.transcript.integration,
                "id": si.transcript.id,
                "from_hash": from_hash,
                "to_hash": to_hash,
            }
        )
        fm["source_transcripts"] = src

        existing = fm.get("transcripts") or []
        if not isinstance(existing, list):
            existing = []
        uuid_list = [u for u in existing if u != si.transcript.id]
        uuid_list.append(si.transcript.id)
        if len(uuid_list) > _TRANSCRIPTS_CAP:
            uuid_list = uuid_list[-_TRANSCRIPTS_CAP:]
        fm["transcripts"] = uuid_list

    if si.scope_redirected_from and "scope_redirected_from" not in fm:
        fm["scope_redirected_from"] = si.scope_redirected_from

    # Phase C: union files_touched across appends so the next chunk's
    # merge decision compares against the full topic history.
    if si.files_touched:
        existing_files = fm.get("files_touched") or []
        if not isinstance(existing_files, list):
            existing_files = []
        fm["files_touched"] = _dedup_preserving_order(
            list(existing_files) + list(si.files_touched)
        )

    new_section = f"\n\n## {si.description}\n\n{si.body_markdown.rstrip()}\n"
    text_new = _render_markdown(fm, body.rstrip() + new_section)
    atomic_write_text(path, text_new)


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    return text[end + 4 :].lstrip("\n")


def _render_markdown(fm: dict[str, Any], body: str) -> str:
    dumped = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{dumped}\n---\n\n{body.rstrip()}\n"
