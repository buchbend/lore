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
    render_body_sections,
)
from lore_core.types import Scope, TranscriptHandle
from lore_core.wikilinks import sanitize_for_write
from lore_curator.buffer_append import AppendOutcome
from lore_curator.buffer_store import Buffer
from lore_curator.session_filer import _slug

if TYPE_CHECKING:
    from lore_core.run_log import RunLogger


__all__ = ["write_or_update", "STUB_SUMMARY_PLACEHOLDER", "STUB_DESCRIPTION_PLACEHOLDER"]


STUB_SUMMARY_PLACEHOLDER = "_synthesis pending_"
STUB_DESCRIPTION_PLACEHOLDER = "_synthesis pending_"
STUB_FRONTMATTER_STATE = "stub"

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


# ---------------------------------------------------------------------------
# Body / frontmatter rendering
# ---------------------------------------------------------------------------


def _render_body(
    *,
    title_placeholder: str,
    activity_commits: list[str],
    activity_issues_opened: list[str],
    activity_issues_closed: list[str],
) -> str:
    return render_body_sections(BodySections(
        title=title_placeholder,
        summary=STUB_SUMMARY_PLACEHOLDER,
        decisions=[],
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
    files_touched: list[str],
    plans: list[str],
    projects: list[str],
    title_placeholder: str,
    buffer_stem: str,
) -> dict[str, Any]:
    fm: dict[str, Any] = {
        "schema_version": 2,
        "type": "session",
        "state": STUB_FRONTMATTER_STATE,
        "created": work_time.date().isoformat(),
        "last_reviewed": work_time.date().isoformat(),
        "title": title_placeholder,
        "description": STUB_DESCRIPTION_PLACEHOLDER,
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
    if plans:
        fm["plans"] = _dedup_preserving_order(plans)
    if files_touched:
        fm["files_touched"] = _dedup_preserving_order(files_touched)
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
            files_touched=rb.files_touched,
            plans=rb.plans,
            projects=rb.projects,
            title_placeholder=title_placeholder,
            buffer_stem=buffer.stem,
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
            files_touched=rb.files_touched,
            plans=rb.plans,
            projects=rb.projects,
            title_placeholder=title_placeholder,
            buffer_stem=buffer.stem,
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
    body_text = strip_frontmatter(text)  # noqa: F841 — kept for parity with _append_to_note;
    # we intentionally re-render the body from the replay rather than
    # parse-merge it. Stub bodies are deterministic snapshots of the
    # buffer; the replay IS the source of truth.

    fm["last_reviewed"] = work_time.date().isoformat()
    fm["curator_a_run"] = now.isoformat()
    fm.setdefault("state", STUB_FRONTMATTER_STATE)
    fm.setdefault("buffer_stem", buffer.stem)
    fm.setdefault("type", "session")
    fm.setdefault("schema_version", 2)
    fm.setdefault("scope", scope.scope)
    fm.setdefault("title", title_placeholder)
    fm.setdefault("description", STUB_DESCRIPTION_PLACEHOLDER)
    if handle_label:
        fm.setdefault("user", handle_label)

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
    if rb.files_touched:
        fm["files_touched"] = _dedup_preserving_order(rb.files_touched)
    if rb.plans:
        fm["plans"] = _dedup_preserving_order(rb.plans)
    if rb.projects:
        fm["projects"] = _dedup_preserving_order(rb.projects)

    body = _render_body(
        title_placeholder=fm.get("title", title_placeholder),
        activity_commits=rb.activity_commits,
        activity_issues_opened=rb.activity_issues_opened,
        activity_issues_closed=rb.activity_issues_closed,
    )
    new_text = _render_markdown(fm, body, wiki_root=wiki_root)
    atomic_write_text(path, new_text)

    return StubWriteResult(
        path=path,
        wikilink=f"[[{path.stem}]]",
        is_first_write=False,
        skipped=False,
    )
