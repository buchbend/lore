"""Freshness signal — positive-evidence-only staleness classification.

Pure, side-effect-free module used by retrieval surfaces (MCP server,
SessionStart inject, /lore:context) to classify a note as ``confirmed``
or ``stale-candidate`` based on **named causes** in its frontmatter or
in derived signals (orphan-link cache, personal confirm sidecar).

**Positive-evidence rule.** Age never flags. A missing
``last_confirmed`` does not matter. A note becomes ``stale-candidate``
*only* if at least one of the following is observed:

1. Authored markers in frontmatter:
   - ``status: stale``
   - ``superseded_by: …``
   - ``supersede_candidate: …``
   - ``supersede_candidate_of: …``
2. The note path is in the supplied orphan set (slice 4 wires the cache).

Authored markers take precedence over the orphan signal — they carry
richer reason text and the orphan signal is a fallback. A note with
both reports ``cause: authored_marker``.

Personal confirms (slice 6) can suppress flagging from *soft* markers
(``supersede_candidate``, ``supersede_candidate_of``) but never
override hard team-wide markers (``status: stale``, ``superseded_by``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Literal


# Default recency window for personal confirms (slice 6). Hard-coded
# until #51 lands the typed config knob; the constant is named so
# audit/tests can target it.
PERSONAL_CONFIRM_RECENCY_DAYS = 14


_HARD_MARKER_KEYS = ("status", "superseded_by")
_SOFT_MARKER_KEYS = ("supersede_candidate", "supersede_candidate_of")


@dataclass(frozen=True)
class FreshnessSignal:
    """The classification of one note for one retrieval.

    Fields:
        status: ``confirmed`` (default) or ``stale-candidate``.
        cause: Why a stale-candidate was raised. ``authored_marker`` for
            frontmatter markers, ``orphan_broken`` for orphan-set
            membership. ``"none"`` for confirmed notes (kept as a
            literal rather than ``None`` so JSON consumers see a
            consistent enum).
        reason: Short human-readable explanation, used by the in-passing
            nudge directive (slice 7).
        confirmed_at: Personal sidecar confirm date when present
            (slice 6). Always ``None`` in slice 1.
    """

    status: Literal["confirmed", "stale-candidate"]
    cause: Literal["authored_marker", "orphan_broken", "none"] | None
    reason: str | None
    confirmed_at: date | None


def _first_marker(fm: dict, keys: tuple[str, ...]) -> tuple[str, object] | None:
    """Return the first ``(key, value)`` pair in ``keys`` that is set."""
    for key in keys:
        if key == "status":
            if str(fm.get(key, "")).strip().lower() == "stale":
                return key, fm.get(key)
            continue
        val = fm.get(key)
        if val:
            return key, val
    return None


def _format_marker_reason(key: str, value: object) -> str:
    """Render a short human-readable reason for an authored marker."""
    if key == "status":
        return "marked stale"
    if key == "superseded_by":
        return f"superseded by {value}"
    if key == "supersede_candidate":
        return f"supersede candidate: {value}"
    if key == "supersede_candidate_of":
        return f"supersede candidate of {value}"
    return f"{key}: {value}"


def _has_authored_marker(fm: dict) -> tuple[str, object] | None:
    """Return the first authored marker found, or None."""
    hard = _first_marker(fm, _HARD_MARKER_KEYS)
    if hard is not None:
        return hard
    return _first_marker(fm, _SOFT_MARKER_KEYS)


def _is_soft_only(fm: dict) -> bool:
    """True iff the only authored markers present are soft (suppressible)."""
    if _first_marker(fm, _HARD_MARKER_KEYS) is not None:
        return False
    return _first_marker(fm, _SOFT_MARKER_KEYS) is not None


def _file_mtime_date(note_path: Path) -> date | None:
    try:
        return date.fromtimestamp(note_path.stat().st_mtime)
    except OSError:
        return None


def compute_freshness(
    note_frontmatter: dict,
    note_path: Path,
    wiki_path: Path,
    sidecar_confirmed_at: date | None = None,
    orphan_set: set[Path] | None = None,
    *,
    today: date | None = None,
    recency_days: int = PERSONAL_CONFIRM_RECENCY_DAYS,
) -> FreshnessSignal:
    """Classify ``note_path`` as confirmed or stale-candidate.

    Pure: no I/O against the note body. The only filesystem touch is
    the optional ``stat`` for mtime gating of personal confirms — the
    caller can pass a pre-computed ``sidecar_confirmed_at`` and
    ``orphan_set`` to keep the function fully decoupled.

    Args:
        note_frontmatter: Parsed YAML frontmatter dict (empty dict for
            notes without frontmatter).
        note_path: Absolute path to the note (used for orphan-set
            membership and personal-confirm mtime gating only).
        wiki_path: Absolute path to the wiki root (reserved for future
            cross-note lookups; unused in slice 1).
        sidecar_confirmed_at: The current user's most recent personal
            confirm for this note, or ``None`` if no confirm exists.
        orphan_set: Set of absolute note paths flagged as containing
            broken wikilinks. Pass an empty set or ``None`` to skip the
            orphan check.
        today: Override "today's date" for recency-window math. Defaults
            to ``date.today()``. Tests pass an explicit date.
        recency_days: Override the personal-confirm recency window.
            Defaults to ``PERSONAL_CONFIRM_RECENCY_DAYS``.

    Returns:
        A :class:`FreshnessSignal`.
    """
    fm = note_frontmatter or {}
    today = today or date.today()
    orphan_set = orphan_set or set()

    authored = _has_authored_marker(fm)

    # Personal confirm suppression: only suppresses *soft-only*
    # authored markers. Hard markers (status:stale, superseded_by) and
    # orphan-broken signals are NEVER suppressed by personal confirms.
    if sidecar_confirmed_at is not None and _is_soft_only(fm):
        mtime = _file_mtime_date(note_path)
        within_window = (today - sidecar_confirmed_at) <= timedelta(days=recency_days)
        not_yet_invalidated = mtime is None or sidecar_confirmed_at >= mtime
        if within_window and not_yet_invalidated:
            return FreshnessSignal(
                status="confirmed",
                cause="none",
                reason=None,
                confirmed_at=sidecar_confirmed_at,
            )

    if authored is not None:
        key, value = authored
        return FreshnessSignal(
            status="stale-candidate",
            cause="authored_marker",
            reason=_format_marker_reason(key, value),
            confirmed_at=sidecar_confirmed_at,
        )

    if note_path in orphan_set:
        return FreshnessSignal(
            status="stale-candidate",
            cause="orphan_broken",
            reason="contains a broken wikilink",
            confirmed_at=sidecar_confirmed_at,
        )

    return FreshnessSignal(
        status="confirmed",
        cause="none",
        reason=None,
        confirmed_at=sidecar_confirmed_at,
    )


def signal_to_dict(signal: FreshnessSignal) -> dict:
    """Render a FreshnessSignal as a JSON-friendly dict for MCP responses."""
    return {
        "status": signal.status,
        "cause": signal.cause,
        "reason": signal.reason,
        "confirmed_at": signal.confirmed_at.isoformat() if signal.confirmed_at else None,
    }
