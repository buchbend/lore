"""Live deterministic stub note for the buffer-and-flush curator.

Owns the markdown file at the canonical session path between first
heartbeat and Phase 1 of flush. Each heartbeat:

- First call: derives a slug from the deterministic signal hierarchy
  (first commit subject → first files_touched basename → ``session-<scope>-<HHMM>``),
  picks the canonical path under ``sessions[/<handle>]/<YYYY>/<MM>/``,
  writes the initial stub, and stamps the path back into the buffer
  sidecar as ``stub_path``. The slug never changes.
- Subsequent calls: replay the buffer, fold the union of
  files_touched / plans / projects / Activity bullets, re-render the
  body in place at the recorded ``stub_path``. Bumps frontmatter
  ``last_reviewed``, appends per-chunk ``source_transcripts`` entries,
  preserves prior fields. Skips the rewrite when ``accumulators_unchanged``
  is reported by the caller (cheap heartbeat path).

Frontmatter contract for a stub:

- ``state: stub`` — Phase 1 of flush drops this marker.
- ``title: <scope> session — <date>`` — placeholder; Phase 2 rewrites.
- ``description: _synthesis pending_`` — placeholder; Phase 2 rewrites.
- Activity sub-sections (Commits / Issues opened / Issues closed) and
  ``files_touched`` / ``plans`` / ``projects`` are populated immediately
  from the deterministic accumulators.
- Body sections ``## Decisions made`` / ``## What we worked on`` /
  ``## Loose ends`` stay empty until Phase 2.

The deterministic-final note left behind on Phase 2 LLM failure is
exactly the same shape minus ``state: stub``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from lore_core.identity import session_note_dir
from lore_core.io import atomic_write_text
from lore_core.schema import parse_frontmatter, strip_frontmatter
from lore_core.session_writer import (
    BodySections,
    parse_body_sections,
    render_body_sections,
)
from lore_core.types import Scope, TranscriptHandle
from lore_core.wikilinks import sanitize_for_write
from lore_curator.buffer_append import AppendOutcome
from lore_curator.buffer_store import Buffer, ReplayedBuffer, Sidecar
from lore_curator.session_filer import _slug

if TYPE_CHECKING:
    from lore_core.note_document import Chapter
    from lore_core.run_log import RunLogger


__all__ = [
    "write_or_update",
    "STUB_SUMMARY_PLACEHOLDER",
    "STUB_DESCRIPTION_PLACEHOLDER",
    "STUB_NARRATIVE_SENTINEL",
    "file_lists_for_frontmatter",
]


STUB_SUMMARY_PLACEHOLDER = "_synthesis pending_"
STUB_DESCRIPTION_PLACEHOLDER = "_synthesis pending_"
STUB_FRONTMATTER_STATE = "stub"
# Frontmatter sentinel: present (`narrative: pending`) whenever the
# body / description carry the deterministic preview text. Phase 2
# pops it once the LLM narrative is in place; the heartbeat rewrite
# branch refreshes the preview only while the sentinel is set.
STUB_NARRATIVE_SENTINEL = "pending"

_TRANSCRIPTS_CAP = 20

# Mirrors render_commits_section's bullet shape:
#   - `<short_hash>` <subject> (<repo>/<branch>)?
# We pull the subject so the slug derivation can use it without
# re-running the git collectors.
_COMMIT_BULLET_RE = re.compile(
    r"^\s*-\s+`[0-9a-f]+`\s+(?P<subject>.+?)(?:\s+\([^)]+\))?\s*$"
)


def _commit_subject_from_bullet(bullet: str) -> str:
    """Pull the subject text out of a ``- `<sha>` <subject>`` line."""
    m = _COMMIT_BULLET_RE.match(bullet)
    return m.group("subject") if m else ""


def _basename_no_ext(path: str) -> str:
    """Last path segment, with the file extension dropped."""
    name = Path(path).name
    dot = name.rfind(".")
    if dot > 0:
        name = name[:dot]
    return name


def _placeholder_title(scope: Scope, work_time: datetime) -> str:
    return f"{scope.scope} session — {work_time.date().isoformat()}"


def _derive_slug(
    *,
    activity: dict[str, Any],
    files_touched: list[str],
    scope: Scope,
    work_time: datetime,
) -> str:
    """Return the stub's filename slug from the first deterministic signal.

    Hierarchy:
    1. First commit subject (parsed out of the ``### Commits`` bullet).
    2. First ``files_touched`` basename.
    3. ``session-<scope>-<HHMM>`` fallback (collision-resistant per minute).
    """
    commit_bullets = activity.get("commits") or []
    for bullet in commit_bullets:
        subject = _commit_subject_from_bullet(bullet)
        if subject.strip():
            return _slug(subject)

    for path in files_touched:
        base = _basename_no_ext(path)
        if base.strip():
            return _slug(base)

    scope_label = scope.scope.replace(":", "-")
    return _slug(f"session-{scope_label}-{work_time.strftime('%H%M')}")


def _lead_for_rename(chapter: Chapter) -> str:
    """First block's topic text, for renaming a note after its first chapter.

    A composed note's file is created (and first named) at the first
    heartbeat — well before any chapter exists, so the initial filename is
    always the heuristic guess above. Once the first chapter composes, its
    opening block names the session's actual topic and the file is renamed
    to match (see ``chapter_flush._rename_to_topic_slug``). Returns ``""``
    when the chapter has no blocks or the first one carries no usable text
    (a continuation block's topic lives in ``continued_topic``) — the
    caller must treat that as "keep the current filename", never invent an
    empty slug.
    """
    if not chapter.blocks:
        return ""
    block = chapter.blocks[0]
    lead = (block.continued_topic if block.continued else block.lead) or ""
    return lead.strip()


def _resolve_renamed_path(note_path: Path, slug: str) -> Path:
    """Return the rename target for ``note_path`` once ``slug`` is known.

    Preserves the ``<DD>-<HHMM>-`` prefix and probes the same directory for
    a free filename exactly like :func:`_resolve_first_write_path`'s
    same-minute collision handling (numeric ``-2``, ``-3`` ... suffix) —
    two notes composed in the same minute must never collide. Returns
    ``note_path`` unchanged when its stem isn't the canonical
    ``<DD>-<HHMM>-<slug>`` shape, or when ``slug`` already matches the
    current one (nothing to rename).
    """
    parts = note_path.stem.split("-", 2)
    if len(parts) < 3:
        return note_path
    day, hhmm, current_slug = parts
    if current_slug == slug:
        return note_path
    prefix = f"{day}-{hhmm}-"
    parent = note_path.parent
    candidate = parent / f"{prefix}{slug}.md"
    counter = 1
    while candidate.exists() and candidate != note_path:
        counter += 1
        candidate = parent / f"{prefix}{slug}-{counter}.md"
    return candidate


# ---------------------------------------------------------------------------
# Live-stub preview rendering
# ---------------------------------------------------------------------------


def _pluralize(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def _wikilinks_for_projects(projects: list[str]) -> str:
    return ", ".join(f"[[{p}]]" for p in projects)


def _format_duration(seconds: int) -> str:
    """Render a human-friendly duration ("12s", "5m", "1h 23m")."""
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    rem_minutes = minutes % 60
    if rem_minutes == 0:
        return f"{hours}h"
    return f"{hours}h {rem_minutes}m"


def _parse_iso_z(timestamp: str) -> datetime | None:
    if not timestamp:
        return None
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _stub_duration_seconds(sidecar: Sidecar) -> int:
    """Seconds between buffer creation and the most recent heartbeat."""
    start = _parse_iso_z(sidecar.created_at)
    end = (
        _parse_iso_z(sidecar.last_heartbeat)
        or _parse_iso_z(sidecar.last_appended_at)
    )
    if start is None or end is None:
        return 0
    return max(0, int((end - start).total_seconds()))


def _render_stub_summary_block(rb: ReplayedBuffer, sidecar: Sidecar) -> str:
    """Render the live-stub ``## Summary`` body block.

    Two italic paragraphs: a fixed framing line ("this is a live stub")
    followed by a stats line built from the union accumulator + sidecar
    counters / timestamps. Pure over the inputs — no I/O, no clock read.
    """
    intro = (
        "_This is a live stub. Counts below refresh each heartbeat; "
        "full narrative is written when the session ends._"
    )

    turn_count = rb.turn_count or sidecar.counters.turn_count
    duration = _format_duration(_stub_duration_seconds(sidecar))
    parts: list[str] = [
        f"{_pluralize(turn_count, 'turn', 'turns')} over {duration}"
    ]

    commits = len(rb.activity_commits)
    if commits:
        parts.append(_pluralize(commits, "commit", "commits"))

    issues_segments: list[str] = []
    opened = len(rb.activity_issues_opened)
    closed = len(rb.activity_issues_closed)
    if opened:
        issues_segments.append(_pluralize(opened, "issue opened", "issues opened"))
    if closed:
        issues_segments.append(_pluralize(closed, "issue closed", "issues closed"))
    if issues_segments:
        parts.append(", ".join(issues_segments))

    files_segments: list[str] = []
    modified = len(rb.files_modified)
    read = len(rb.files_read)
    if modified:
        files_segments.append(_pluralize(modified, "file modified", "files modified"))
    if read:
        files_segments.append(_pluralize(read, "file read", "files read"))
    if files_segments:
        parts.append(", ".join(files_segments))

    if rb.projects:
        parts.append(f"projects {_wikilinks_for_projects(rb.projects)}")

    stats_line = "_So far: " + " · ".join(parts) + "._"
    return f"{intro}\n\n{stats_line}"


def _render_stub_description(rb: ReplayedBuffer, sidecar: Sidecar) -> str:
    """Render the frontmatter ``description`` one-liner for a live stub."""
    turn_count = rb.turn_count or sidecar.counters.turn_count
    parts: list[str] = [_pluralize(turn_count, "turn", "turns")]

    commits = len(rb.activity_commits)
    if commits:
        parts.append(_pluralize(commits, "commit", "commits"))

    modified = len(rb.files_modified)
    if modified:
        parts.append(_pluralize(modified, "file modified", "files modified"))

    if rb.projects:
        prefix = f"Live stub in {_wikilinks_for_projects(rb.projects)}"
    else:
        prefix = "Live stub"

    return f"{prefix} — {', '.join(parts)}; full narrative written at session end."


# ---------------------------------------------------------------------------
# Body / frontmatter rendering
# ---------------------------------------------------------------------------


def _render_body(
    *,
    title_placeholder: str,
    summary: str,
    activity_commits: list[str],
    activity_issues_opened: list[str],
    activity_issues_closed: list[str],
) -> str:
    return render_body_sections(BodySections(
        title=title_placeholder,
        summary=summary,
        adr_candidates=[],
        worked_on=[],
        loose_ends=[],
        commits=activity_commits,
        issues_opened=activity_issues_opened,
        issues_closed=activity_issues_closed,
    ))


def _render_markdown(fm: dict[str, Any], body: str, *, wiki_root: Path) -> str:
    dumped = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    text = f"---\n{dumped}\n---\n\n{body.rstrip()}\n"
    return sanitize_for_write(text, wiki_root)


def _dedup_preserving_order(items: list[str] | None) -> list[str]:
    if not items:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if isinstance(item, str) and item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def file_lists_for_frontmatter(
    files_modified: list[str] | None,
    files_read: list[str] | None,
) -> dict[str, list[str]]:
    """Return the ``files_modified`` / ``files_read`` frontmatter pair.

    Single source of truth for the "suppress reads when subsumed" rule:

    - ``files_modified`` is included verbatim (deduped) when non-empty.
    - ``files_read`` is included only for paths that were NOT also
      modified — keeps editing-session frontmatter tidy in the dominant
      case where every edited file was read first.
    - Both lists are uncapped (load-bearing for retrieval recall on
      file-name queries in read-heavy interview sessions).

    Returns ``{}`` when both inputs are empty.
    """
    out: dict[str, list[str]] = {}
    modified_dedup = _dedup_preserving_order(list(files_modified or []))
    if modified_dedup:
        out["files_modified"] = modified_dedup
    modified_set = set(modified_dedup)
    read_extra = [f for f in (files_read or []) if f not in modified_set]
    read_dedup = _dedup_preserving_order(read_extra)
    if read_dedup:
        out["files_read"] = read_dedup
    return out


def _build_first_write_frontmatter(
    *,
    scope: Scope,
    handle_label: str,
    work_time: datetime,
    now: datetime,
    transcript: TranscriptHandle,
    transcript_id: str,
    integration: str,
    chunk_from_hash: str,
    chunk_to_hash: str,
    files_modified: list[str],
    files_read: list[str],
    projects: list[str],
    title_placeholder: str,
    buffer_stem: str,
    description: str,
) -> dict[str, Any]:
    fm: dict[str, Any] = {
        "schema_version": 2,
        "type": "session",
        "state": STUB_FRONTMATTER_STATE,
        "narrative": STUB_NARRATIVE_SENTINEL,
        "created": work_time.date().isoformat(),
        "last_reviewed": work_time.date().isoformat(),
        "title": title_placeholder,
        "description": description,
        "scope": scope.scope,
    }
    if handle_label:
        fm["user"] = handle_label
    fm["curator_a_run"] = now.isoformat()
    fm["source_transcripts"] = [
        {
            "integration": integration,
            "id": transcript_id,
            "from_hash": chunk_from_hash,
            "to_hash": chunk_to_hash,
        }
    ]
    fm["transcripts"] = [transcript_id]
    if projects:
        fm["projects"] = _dedup_preserving_order(projects)
    fm.update(file_lists_for_frontmatter(files_modified, files_read))
    fm["buffer_stem"] = buffer_stem
    return fm


def _resolve_first_write_path(
    *,
    wiki_root: Path,
    handle_label: str,
    work_time: datetime,
    slug: str,
) -> Path:
    sessions_base = session_note_dir(wiki_root, handle_label)
    month_dir = sessions_base / str(work_time.year) / f"{work_time.month:02d}"
    month_dir.mkdir(parents=True, exist_ok=True)
    day_prefix = f"{work_time.day:02d}"
    time_prefix = work_time.strftime("%H%M")
    candidate = month_dir / f"{day_prefix}-{time_prefix}-{slug}.md"
    counter = 1
    while candidate.exists():
        counter += 1
        candidate = month_dir / f"{day_prefix}-{time_prefix}-{slug}-{counter}.md"
    return candidate


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


@dataclass
class StubWriteResult:
    path: Path
    wikilink: str
    is_first_write: bool
    skipped: bool


def write_or_update(
    *,
    outcome: AppendOutcome,
    scope: Scope,
    transcript: TranscriptHandle,
    wiki_root: Path,
    work_time: datetime,
    now: datetime,
    integration: str,
    handle_label: str = "",
    chunk_from_hash: str = "",
    chunk_to_hash: str = "",
    logger: "RunLogger | None" = None,
) -> StubWriteResult | None:
    """Write or update the live stub note for ``outcome``'s buffer.

    Returns ``None`` when there's nothing to do (no-op append from
    ``buffer_append`` or unchanged accumulators on a subsequent call —
    the existing stub is left untouched aside from the heartbeat-only
    sidecar fields, which ``buffer_append`` already patched).

    Writes (first time) or rewrites (subsequent times) the markdown file
    at the path recorded in ``buffer.stub_path``. Frontmatter
    accumulator fields (``files_touched``, ``plans``, ``projects``)
    are unioned across heartbeats so the stub is a faithful snapshot of
    the deterministic union, even when the LLM never composes the final
    narrative.
    """
    if outcome.skipped_no_op:
        return None

    buffer = outcome.buffer
    sidecar = outcome.sidecar_after or buffer.read_sidecar()
    if sidecar is None:
        return None

    transcript_id = sidecar.transcript_id or transcript.id

    # Replay the (now-post-append) buffer for the union view of
    # accumulators. The body renders strictly from this view; the
    # incoming chunk's data is already represented in the replay.
    rb = buffer.replay()

    title_placeholder = _placeholder_title(scope, work_time)

    # ------------------------------------------------------------------
    # First write — pick path, derive slug, stamp sidecar.stub_path
    # ------------------------------------------------------------------
    if not sidecar.stub_path:
        slug = _derive_slug(
            activity=outcome.activity,
            files_touched=outcome.files_touched,
            scope=scope,
            work_time=work_time,
        )
        path = _resolve_first_write_path(
            wiki_root=wiki_root,
            handle_label=handle_label,
            work_time=work_time,
            slug=slug,
        )
        body = _render_body(
            title_placeholder=title_placeholder,
            summary=_render_stub_summary_block(rb, sidecar),
            activity_commits=rb.activity_commits,
            activity_issues_opened=rb.activity_issues_opened,
            activity_issues_closed=rb.activity_issues_closed,
        )
        fm = _build_first_write_frontmatter(
            scope=scope,
            handle_label=handle_label,
            work_time=work_time,
            now=now,
            transcript=transcript,
            transcript_id=transcript_id,
            integration=integration,
            chunk_from_hash=chunk_from_hash,
            chunk_to_hash=chunk_to_hash,
            files_modified=rb.files_modified,
            files_read=rb.files_read,
            projects=rb.projects,
            title_placeholder=title_placeholder,
            buffer_stem=buffer.stem,
            description=_render_stub_description(rb, sidecar),
        )
        text = _render_markdown(fm, body, wiki_root=wiki_root)
        atomic_write_text(path, text)

        with buffer.with_lock():
            buffer.patch(stub_path=str(path))

        wikilink = f"[[{path.stem}]]"
        if logger is not None:
            logger.emit(
                "stub-note-created",
                transcript_id=transcript_id,
                path=str(path),
                wikilink=wikilink,
                buffer_stem=buffer.stem,
            )
        return StubWriteResult(
            path=path,
            wikilink=wikilink,
            is_first_write=True,
            skipped=False,
        )

    # ------------------------------------------------------------------
    # Subsequent rewrite — replay-driven, in-place
    # ------------------------------------------------------------------
    path = Path(sidecar.stub_path)
    if not path.exists():
        # Stub was deleted (manual cleanup, test fixture). Re-create at
        # the recorded path with the union accumulator.
        body = _render_body(
            title_placeholder=title_placeholder,
            summary=_render_stub_summary_block(rb, sidecar),
            activity_commits=rb.activity_commits,
            activity_issues_opened=rb.activity_issues_opened,
            activity_issues_closed=rb.activity_issues_closed,
        )
        fm = _build_first_write_frontmatter(
            scope=scope,
            handle_label=handle_label,
            work_time=work_time,
            now=now,
            transcript=transcript,
            transcript_id=transcript_id,
            integration=integration,
            chunk_from_hash=chunk_from_hash,
            chunk_to_hash=chunk_to_hash,
            files_modified=rb.files_modified,
            files_read=rb.files_read,
            projects=rb.projects,
            title_placeholder=title_placeholder,
            buffer_stem=buffer.stem,
            description=_render_stub_description(rb, sidecar),
        )
        text = _render_markdown(fm, body, wiki_root=wiki_root)
        atomic_write_text(path, text)
        return StubWriteResult(
            path=path,
            wikilink=f"[[{path.stem}]]",
            is_first_write=False,
            skipped=False,
        )

    if outcome.accumulators_unchanged:
        # Body would render identically; skip the disk write. The
        # sidecar's last_heartbeat already advanced via buffer_append's
        # transition call.
        return StubWriteResult(
            path=path,
            wikilink=f"[[{path.stem}]]",
            is_first_write=False,
            skipped=True,
        )

    text = path.read_text()
    fm = parse_frontmatter(text)
    body_text = strip_frontmatter(text)
    # ``synth_in_place`` may have applied a Phase-2 narrative against a
    # live (still-accumulating) buffer. We must NOT clobber that
    # narrative on the next heartbeat: parse the existing body and
    # reuse its narrative sections (title, summary, decisions, worked_on,
    # discussion, loose_ends). Activity sub-sections (commits / issues)
    # always refresh from the deterministic replay — they're the
    # whole point of the heartbeat rewrite.
    existing_body = parse_body_sections(body_text)

    fm["last_reviewed"] = work_time.date().isoformat()
    fm["curator_a_run"] = now.isoformat()
    fm.setdefault("state", STUB_FRONTMATTER_STATE)
    fm.setdefault("buffer_stem", buffer.stem)
    fm.setdefault("type", "session")
    fm.setdefault("schema_version", 2)
    fm.setdefault("scope", scope.scope)
    fm.setdefault("title", title_placeholder)
    if handle_label:
        fm.setdefault("user", handle_label)

    # Refresh the live-stub preview only while the narrative sentinel
    # is set. Once Phase 2 has run (sentinel popped, LLM summary +
    # description in place), heartbeats must NOT clobber the narrative
    # — they only refresh deterministic accumulators below.
    preview_active = fm.get("narrative") == STUB_NARRATIVE_SENTINEL
    if preview_active:
        fm["description"] = _render_stub_description(rb, sidecar)
    else:
        fm.setdefault("description", STUB_DESCRIPTION_PLACEHOLDER)

    # Per-chunk transcript provenance — append source_transcripts for
    # this heartbeat (matches the legacy _append_to_note shape).
    src = fm.get("source_transcripts") or []
    if not isinstance(src, list):
        src = []
    src.append({
        "integration": integration,
        "id": transcript_id,
        "from_hash": chunk_from_hash,
        "to_hash": chunk_to_hash,
    })
    fm["source_transcripts"] = src

    existing_uuids = fm.get("transcripts") or []
    if not isinstance(existing_uuids, list):
        existing_uuids = []
    uuid_list = [u for u in existing_uuids if u != transcript_id]
    uuid_list.append(transcript_id)
    if len(uuid_list) > _TRANSCRIPTS_CAP:
        uuid_list = uuid_list[-_TRANSCRIPTS_CAP:]
    fm["transcripts"] = uuid_list

    # Frontmatter accumulator union — replay is authoritative.
    # New schema: ``files_modified`` (edits only) + ``files_read`` (reads
    # not subsumed by edits). Drops ``files_touched`` from new writes;
    # legacy notes filed before this change keep theirs as an opaque
    # union (read-side fallback in the merge gate handles them).
    fm.pop("files_touched", None)
    fm.update(file_lists_for_frontmatter(rb.files_modified, rb.files_read))
    if rb.projects:
        fm["projects"] = _dedup_preserving_order(rb.projects)

    body_title = existing_body.title or fm.get("title") or title_placeholder
    if preview_active:
        body_summary = _render_stub_summary_block(rb, sidecar)
        body_decisions: list[str] = []
        body_worked_on: list[str] = []
        body_loose_ends: list[str] = []
        body_discussion: list[str] = []
    else:
        body_summary = existing_body.summary or STUB_SUMMARY_PLACEHOLDER
        body_decisions = existing_body.adr_candidates
        body_worked_on = existing_body.worked_on
        body_loose_ends = existing_body.loose_ends
        body_discussion = existing_body.discussion
    body = render_body_sections(BodySections(
        title=body_title,
        summary=body_summary,
        adr_candidates=body_decisions,
        worked_on=body_worked_on,
        loose_ends=body_loose_ends,
        commits=rb.activity_commits,
        issues_opened=rb.activity_issues_opened,
        issues_closed=rb.activity_issues_closed,
        discussion=body_discussion,
    ))
    new_text = _render_markdown(fm, body, wiki_root=wiki_root)
    atomic_write_text(path, new_text)

    return StubWriteResult(
        path=path,
        wikilink=f"[[{path.stem}]]",
        is_first_write=False,
        skipped=False,
    )
