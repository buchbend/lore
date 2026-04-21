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
    """Per-curator slice of state. role ∈ {'a', 'b', 'c'}."""

    role: str
    last_run_ts: datetime | None
    last_run_notes_new: int | None
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


def _last_run_summary(
    lore_root: Path,
) -> tuple[datetime | None, int | None, int | None, str | None, tuple[datetime, str] | None]:
    """Return (last_run_ts, last_notes_new, last_errors, last_short_id, last_note_filed).

    "Last note filed" walks newest→oldest runs for the first session-note
    record with action=filed.
    """
    from lore_core.run_reader import iter_archival_runs

    last_ts: datetime | None = None
    last_notes_new: int | None = None
    last_errors: int | None = None
    last_short_id: str | None = None
    last_note: tuple[datetime, str] | None = None

    for i, path in enumerate(iter_archival_runs(lore_root)):
        try:
            lines = path.read_text().splitlines()
        except OSError:
            continue

        # Walk backwards inside the file looking for run-end + session-note.
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
            last_ts = _parse_iso(run_end.get("ts"))
            last_notes_new = run_end.get("notes_new")
            last_errors = run_end.get("errors")
            last_short_id = path.stem.split("-")[-1]

        if last_note is not None and i > 0:
            break  # note was found in a prior run; no need to walk further

    return (last_ts, last_notes_new, last_errors, last_short_id, last_note)


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


def _per_role_last_run(lore_root: Path, role: str) -> datetime | None:
    """Read last_curator_{role} from the first WikiLedger we can find.

    For vaults with multiple wikis this is an approximation: we take the
    newest last_curator_{role} across all wikis. That's what the user
    cares about for "is the curator alive?" — not which specific wiki.
    """
    from lore_core.ledger import WikiLedger
    wiki_dir = lore_root / "wiki"
    if not wiki_dir.exists():
        return None
    newest: datetime | None = None
    for w in sorted(p for p in wiki_dir.iterdir() if p.is_dir()):
        try:
            entry = WikiLedger(lore_root, w.name).read()
        except Exception:
            continue
        ts = getattr(entry, f"last_curator_{role}", None)
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if newest is None or ts > newest:
            newest = ts
    return newest


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

    last_ts, last_notes, last_errors, last_short, last_note = _last_run_summary(lore_root)
    work_lock = _work_lock_held(lore_root)

    curators: list[CuratorStatus] = []
    for role in ("a", "b", "c"):
        per_role_ts = _per_role_last_run(lore_root, role)
        # For role A only, fall back to the overall "last_ts" from the run log
        # if the WikiLedger has no per-role entry yet. B and C are calendar-
        # scheduled and should rely on their ledger entries explicitly.
        effective_ts = per_role_ts if per_role_ts is not None else (last_ts if role == "a" else None)
        curators.append(
            CuratorStatus(
                role=role,
                last_run_ts=effective_ts,
                last_run_notes_new=last_notes if role == "a" else None,
                last_run_errors=last_errors if role == "a" else None,
                last_run_short_id=last_short if role == "a" else None,
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
        pending_transcripts=_pending_transcripts(lore_root),
        hook_errors_24h=_count_hook_errors_24h(lore_root, now),
        hook_log_failed_marker_age_s=_marker_age_s(lore_root, now),
        simple_tier_fallback_active=_simple_tier_fallback_active(lore_root),
    )
