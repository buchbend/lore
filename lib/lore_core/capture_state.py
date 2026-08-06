"""Single-source-of-truth snapshot of "what is lore doing right now?".

``query_capture_state(lore_root, cwd=...)`` returns a frozen ``CaptureState``
that everything user-facing can render against:

- ``lore status`` — activity-first CLI
- ``lore doctor``'s capture panel (install-mode footer pointer only —
  doctor itself doesn't render it)
- SessionStart banner
- ``/lore:context`` live-state section

Read-only by construction. The query opens files for reading but never
writes, so it's safe to call from any context (including repeatedly during
a single render).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from lore_core.timefmt import parse_ts


@dataclass(frozen=True)
class CaptureState:
    lore_root: Path
    scope_attached: bool
    scope_name: str | None                        # e.g. "private/lore"
    scope_root: Path | None                       # parent of the CLAUDE.md
    # Newest curator run on the spine. Hygiene is the only producer, so
    # the fields carry no role: they answer "did the last run fail?", the
    # one question the banner asks of them.
    last_run_ts: datetime | None = None
    last_run_errors: int | None = None
    last_run_short_id: str | None = None          # gates the last-run-errors banner line
    hook_errors_24h: int = 0
    spine_write_failed_marker_age_s: int | None = None
    simple_tier_fallback_active: bool = False
    # Liveness of the capture hook itself — answers "did Claude Code actually
    # invoke our SessionStart/PreCompact/SessionEnd hook recently?". All
    # three fields come from the newest hook record on the event spine.
    last_hook_event_ts: datetime | None = None
    last_hook_event_outcome: str | None = None    # e.g. "spawned-curator" | "no-scope"
    last_hook_event_kind: str | None = None       # e.g. "session-start"


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
    errors: int | None
    short_id: str | None


def _last_run_summary(lore_root: Path) -> _RunSummary:
    """Return the newest curator run's end record, or an empty summary."""
    from lore_core.run_reader import read_curator_runs

    grouped = read_curator_runs(lore_root)
    if not grouped:
        return _RunSummary(None, None, None)

    # run_id is timestamp-prefixed, so the max key is the newest run.
    run_id = max(grouped)
    run_end = next(
        (rec for rec in reversed(grouped[run_id]) if rec.get("type") == "run-end"), None
    )
    if run_end is None:
        return _RunSummary(None, None, None)
    return _RunSummary(
        ts=parse_ts(run_end.get("ts")),
        errors=run_end.get("errors"),
        short_id=run_id.split("-")[-1],
    )


def _count_hook_errors_24h(lore_root: Path, now: datetime) -> int:
    from lore_core.spine import read_spine

    threshold = now - timedelta(hours=24)
    count = 0
    for rec in read_spine(lore_root, source="hook"):
        if rec.get("level") != "error":
            continue
        ts = parse_ts(rec.get("ts"))
        if ts is not None and ts >= threshold:
            count += 1
    return count


def _newest_hook_event(
    lore_root: Path,
) -> tuple[datetime | None, str | None, str | None]:
    """Return (ts, outcome, event-kind) of the newest hook record on the
    event spine, or (None, None, None) if there are no parseable records.

    Scans the whole file because records are append-only but not
    guaranteed to be strictly time-ordered (rotations, clock skew).
    At ~10 MB rotation cap this is fine. ``outcome`` is a hook-domain
    field living in the envelope ``data``; ``event`` is the record kind.
    """
    from lore_core.spine import read_spine

    newest_ts: datetime | None = None
    newest_outcome: str | None = None
    newest_kind: str | None = None
    for rec in read_spine(lore_root, source="hook"):
        ts = parse_ts(rec.get("ts"))
        if ts is None:
            continue
        if newest_ts is None or ts > newest_ts:
            newest_ts = ts
            newest_outcome = (rec.get("data") or {}).get("outcome")
            newest_kind = rec.get("event")
    return (newest_ts, newest_outcome, newest_kind)


def _marker_age_s(lore_root: Path, now: datetime) -> int | None:
    marker = lore_root / ".lore" / "spine-failed.marker"
    if not marker.exists():
        return None
    try:
        mtime = datetime.fromtimestamp(marker.stat().st_mtime, tz=UTC)
    except OSError:
        return None
    return max(0, int((now - mtime).total_seconds()))


def _simple_tier_fallback_active(lore_root: Path) -> bool:
    return (lore_root / ".lore" / "warnings.log").exists()


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

    summary = _last_run_summary(lore_root)
    last_hook_ts, last_hook_outcome, last_hook_kind = _newest_hook_event(lore_root)

    return CaptureState(
        lore_root=lore_root,
        scope_attached=attached,
        scope_name=scope_name,
        scope_root=scope_root,
        last_run_ts=summary.ts,
        last_run_errors=summary.errors,
        last_run_short_id=summary.short_id,
        hook_errors_24h=_count_hook_errors_24h(lore_root, now),
        spine_write_failed_marker_age_s=_marker_age_s(lore_root, now),
        simple_tier_fallback_active=_simple_tier_fallback_active(lore_root),
        last_hook_event_ts=last_hook_ts,
        last_hook_event_outcome=last_hook_outcome,
        last_hook_event_kind=last_hook_kind,
    )
