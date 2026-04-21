"""Single-source-of-truth snapshot of "what is lore doing right now?".

``query_capture_state(lore_root, cwd=...)`` returns a frozen ``CaptureState``
that everything user-facing can render against:

- ``lore status`` (Task 11) — activity-first CLI
- ``lore doctor``'s capture panel (Task 12a keeps it only for the install-
  mode footer pointer — doctor itself moves off)
- SessionStart banner (Task 12b)
- ``/lore:loaded`` live-state section (Task 13)
- ``lore runs list`` stays a history view and does NOT render CaptureState.

Read-only by construction. The query opens files for reading but never
writes, so it's safe to call from any context (including repeatedly during
a single render).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path


@dataclass(frozen=True)
class CuratorStatus:
    """Per-curator slice of state. role ∈ {'a', 'b', 'c'}.

    The ``last_run_*`` fields (notes_new, notes_merged, skipped, errors,
    short_id) are populated for role=='a' only — the run-log is emitted
    by Curator A. B and C populate only ``last_run_ts`` (via their own
    ledger entries) and ``overdue``.
    """

    role: str
    last_run_ts: datetime | None
    last_run_notes_new: int | None
    last_run_notes_merged: int | None
    last_run_skipped: int | None
    last_run_errors: int | None
    last_run_short_id: str | None   # for "lore runs show <id>" hint copy
    work_lock_held: bool
    overdue: bool                   # a: >24h, b: calendar-day rollover, c: >7d


@dataclass(frozen=True)
class CaptureState:
    lore_root: Path
    scope_attached: bool
    scope_name: str | None                        # e.g. "private/lore"
    scope_root: Path | None                       # parent of the CLAUDE.md
    curators: list[CuratorStatus] = field(default_factory=list)
    last_note_filed: tuple[datetime, str] | None = None  # (ts, wikilink)
    last_briefing_ts: datetime | None = None  # newest across all WikiLedgers
    pending_transcripts: int = 0
    hook_errors_24h: int = 0
    hook_log_failed_marker_age_s: int | None = None
    simple_tier_fallback_active: bool = False


# Overdue thresholds per project_curator_triad memory.
_A_OVERDUE = timedelta(hours=24)
_C_OVERDUE = timedelta(days=7)


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        out = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return out if out.tzinfo is not None else out.replace(tzinfo=UTC)


def _is_overdue(role: str, last_run_ts: datetime | None, now: datetime) -> bool:
    """True if the next run is due.

    - A never-run curator is overdue by definition (it has work to do the
      first time it's asked).
    """
    if last_run_ts is None:
        return True
    delta = now - last_run_ts
    if role == "a":
        return delta > _A_OVERDUE
    if role == "b":
        return last_run_ts.date() < now.date()
    if role == "c":
        return delta > _C_OVERDUE
    return False


def _resolve_scope(cwd: Path | None) -> tuple[bool, str | None, Path | None]:
    """Return (attached, "wiki/scope", scope_root_path)."""
    if cwd is None:
        return (False, None, None)
    from lore_core.scope_resolver import resolve_scope
    try:
        scope = resolve_scope(cwd)
    except Exception:
        return (False, None, None)
    if scope is None:
        return (False, None, None)
    name = f"{scope.wiki}/{scope.scope}"
    return (True, name, scope.claude_md_path.parent)


@dataclass(frozen=True)
class _RunSummary:
    ts: datetime | None
    notes_new: int | None
    notes_merged: int | None
    skipped: int | None
    errors: int | None
    short_id: str | None


def _last_run_summary(
    lore_root: Path,
) -> tuple[_RunSummary, tuple[datetime, str] | None]:
    """Return (most-recent-run summary, last_note_filed).

    "Last note filed" walks newest→oldest runs for the first session-note
    record with action=filed.
    """
    from lore_core.run_reader import iter_archival_runs

    summary = _RunSummary(None, None, None, None, None, None)
    last_note: tuple[datetime, str] | None = None

    for i, path in enumerate(iter_archival_runs(lore_root)):
        try:
            lines = path.read_text().splitlines()
        except OSError:
            continue

        run_end: dict | None = None
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = rec.get("type")
            if run_end is None and t == "run-end":
                run_end = rec
            if last_note is None and t == "session-note" and rec.get("action") == "filed":
                note_ts = _parse_iso(rec.get("ts"))
                wikilink = rec.get("wikilink")
                if note_ts and wikilink:
                    last_note = (note_ts, wikilink)
            if run_end is not None and last_note is not None:
                break

        if i == 0 and run_end is not None:
            summary = _RunSummary(
                ts=_parse_iso(run_end.get("ts")),
                notes_new=run_end.get("notes_new"),
                notes_merged=run_end.get("notes_merged"),
                skipped=run_end.get("skipped"),
                errors=run_end.get("errors"),
                short_id=path.stem.split("-")[-1],
            )

        if last_note is not None and i > 0:
            break

    return (summary, last_note)


def _count_hook_errors_24h(lore_root: Path, now: datetime) -> int:
    path = lore_root / ".lore" / "hook-events.jsonl"
    if not path.exists():
        return 0
    threshold = now - timedelta(hours=24)
    count = 0
    try:
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("outcome") != "error":
                continue
            ts = _parse_iso(rec.get("ts"))
            if ts is None:
                continue
            if ts >= threshold:
                count += 1
    except OSError:
        return 0
    return count


def _marker_age_s(lore_root: Path, now: datetime) -> int | None:
    marker = lore_root / ".lore" / "hook-log-failed.marker"
    if not marker.exists():
        return None
    try:
        mtime = datetime.fromtimestamp(marker.stat().st_mtime, tz=UTC)
    except OSError:
        return None
    return max(0, int((now - mtime).total_seconds()))


def _pending_transcripts(lore_root: Path) -> int:
    from lore_core.ledger import TranscriptLedger
    try:
        return len(TranscriptLedger(lore_root).pending())
    except Exception:
        return 0


def _simple_tier_fallback_active(lore_root: Path) -> bool:
    return (lore_root / ".lore" / "warnings.log").exists()


def _work_lock_held(lore_root: Path) -> bool:
    return (lore_root / ".lore" / "curator.lock").exists()


def _newest_across_wikis(lore_root: Path, field_name: str) -> datetime | None:
    """Return the newest ``field_name`` across all WikiLedgers in the vault.

    Iterates WikiLedger JSON files directly (``.lore/wiki-<name>-ledger.json``)
    rather than the wiki/ directory — this is robust to vaults where the
    ledger exists but the wiki directory doesn't (e.g. test fixtures).
    """
    from lore_core.ledger import WikiLedger
    lore_dir = lore_root / ".lore"
    if not lore_dir.exists():
        return None
    newest: datetime | None = None
    for ledger_file in lore_dir.glob("wiki-*-ledger.json"):
        # Extract the wiki name: "wiki-{name}-ledger.json"
        stem = ledger_file.stem
        if not (stem.startswith("wiki-") and stem.endswith("-ledger")):
            continue
        wiki_name = stem[len("wiki-"):-len("-ledger")]
        try:
            entry = WikiLedger(lore_root, wiki_name).read()
        except Exception:
            continue
        ts = getattr(entry, field_name, None)
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if newest is None or ts > newest:
            newest = ts
    return newest


def _per_role_last_run(lore_root: Path, role: str) -> datetime | None:
    """Newest last_curator_{role} across all WikiLedgers in the vault."""
    return _newest_across_wikis(lore_root, f"last_curator_{role}")


def query_capture_state(
    lore_root: Path,
    *,
    cwd: Path | None = None,
    now: datetime | None = None,
) -> CaptureState:
    """Return a read-only snapshot of capture-subsystem state.

    All fields computed from on-disk state; no writes. Safe to call from
    any context, including repeatedly during a render.

    ``cwd`` defaults to no-scope-resolution. Pass a directory to populate
    ``scope_attached`` / ``scope_name`` / ``scope_root``.
    """
    if now is None:
        now = datetime.now(UTC)

    attached, scope_name, scope_root = _resolve_scope(cwd)

    summary, last_note = _last_run_summary(lore_root)
    work_lock = _work_lock_held(lore_root)

    curators: list[CuratorStatus] = []
    for role in ("a", "b", "c"):
        per_role_ts = _per_role_last_run(lore_root, role)
        effective_ts = per_role_ts if per_role_ts is not None else (summary.ts if role == "a" else None)
        is_a = role == "a"
        curators.append(
            CuratorStatus(
                role=role,
                last_run_ts=effective_ts,
                last_run_notes_new=summary.notes_new if is_a else None,
                last_run_notes_merged=summary.notes_merged if is_a else None,
                last_run_skipped=summary.skipped if is_a else None,
                last_run_errors=summary.errors if is_a else None,
                last_run_short_id=summary.short_id if is_a else None,
                work_lock_held=work_lock,
                overdue=_is_overdue(role, effective_ts, now),
            )
        )

    return CaptureState(
        lore_root=lore_root,
        scope_attached=attached,
        scope_name=scope_name,
        scope_root=scope_root,
        curators=curators,
        last_note_filed=last_note,
        last_briefing_ts=_newest_across_wikis(lore_root, "last_briefing"),
        pending_transcripts=_pending_transcripts(lore_root),
        hook_errors_24h=_count_hook_errors_24h(lore_root, now),
        hook_log_failed_marker_age_s=_marker_age_s(lore_root, now),
        simple_tier_fallback_active=_simple_tier_fallback_active(lore_root),
    )
