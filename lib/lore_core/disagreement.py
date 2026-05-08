"""Disagreement detection — slice 9 of PRD #65.

A team-mode conflict surfaces when one user marked a note stale
(team-wide frontmatter ``status: stale``) and a second user (or the
same user later) personally confirmed it. The asymmetric storage
means the two verdicts coexist silently; the disagreement detector
spots the contradiction so the in-passing nudge can ask for an
explicit resolution.

Pure: no I/O. Inputs are the parsed frontmatter dict and the (already
loaded) personal-sidecar confirm date.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date


@dataclass(frozen=True)
class Disagreement:
    """A team-stale verdict and a later personal confirm coexist.

    Fields:
        stale_by: Handle that authored the team-stale verdict.
        stale_at: Date the team-stale verdict was written.
        stale_reason: Reason text recorded with the stale verdict.
        self_confirmed_at: The current user's personal confirm date.
    """

    stale_by: str
    stale_at: _date
    stale_reason: str
    self_confirmed_at: _date


def _coerce_date(value) -> _date | None:
    if isinstance(value, _date):
        return value
    if isinstance(value, str):
        try:
            return _date.fromisoformat(value)
        except ValueError:
            return None
    return None


def detect_disagreement(
    note_frontmatter: dict,
    sidecar_confirmed_at: _date | None,
) -> Disagreement | None:
    """Return a :class:`Disagreement` when the two verdicts conflict.

    A disagreement is produced when **all** of these hold:
      * ``note_frontmatter`` contains ``status: stale`` (with
        ``stale_by``, ``stale_at``, ``stale_reason``).
      * ``sidecar_confirmed_at`` is non-null.
      * ``sidecar_confirmed_at > stale_at``.

    Same-user case is intentionally covered: the user may have
    changed their own mind after a stale verdict and not cleared the
    marker; that is still worth surfacing.

    Returns ``None`` (not falsy) when:
      * No personal confirm.
      * No ``status: stale`` marker.
      * The confirm precedes the stale verdict (stale wins).
      * The frontmatter is missing ``stale_at`` (graceful skip — we
        cannot order without a date).
    """
    fm = note_frontmatter or {}
    if str(fm.get("status", "")).strip().lower() != "stale":
        return None
    if sidecar_confirmed_at is None:
        return None
    stale_at = _coerce_date(fm.get("stale_at"))
    if stale_at is None:
        return None
    if sidecar_confirmed_at <= stale_at:
        return None
    return Disagreement(
        stale_by=str(fm.get("stale_by") or ""),
        stale_at=stale_at,
        stale_reason=str(fm.get("stale_reason") or ""),
        self_confirmed_at=sidecar_confirmed_at,
    )


def disagreement_to_dict(d: Disagreement) -> dict:
    """JSON-friendly render for the MCP `freshness.disagreement` field."""
    return {
        "stale_by": d.stale_by,
        "stale_at": d.stale_at.isoformat(),
        "stale_reason": d.stale_reason,
        "self_confirmed_at": d.self_confirmed_at.isoformat(),
    }
