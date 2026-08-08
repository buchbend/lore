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


def _is_pending_from_catalog_entry(
    entry: dict, orphan_paths: set[str]
) -> bool:
    """Coarse candidate filter: could this catalog entry need a verdict?

    Catalog-only signals — ``status: stale`` and ``orphan_set``
    membership. Both are open questions: a stale marker may carry no
    reason yet, and a broken wikilink has no author behind it at all.

    ``superseded_by`` is deliberately absent. Supersession names the
    successor note, so the decision is already recorded; a superseded
    note that also carries a stale marker still qualifies here through
    ``status``. Soft markers (``supersede_candidate``) never qualify.

    The candidate set is only the first of two stages.
    :func:`list_pending_verdicts` reads each candidate's frontmatter
    and drops the ones whose verdict is already recorded
    (:func:`_verdict_recorded`). Retrieval-time classification is a
    separate question and still flows through :func:`compute_freshness`.
    """
    if not isinstance(entry, dict):
        return False
    path = str(entry.get("path") or "")
    return bool(
        str(entry.get("status") or "").lower() == "stale"
        or (path and path in orphan_paths)
    )


def _verdict_recorded(fm: dict) -> bool:
    """True when a human already decided this note's freshness.

    Two forms count. ``superseded_by`` names the successor note.
    ``status: stale`` with a non-empty ``stale_reason`` is the complete
    four-field stale schema. A bare ``status: stale`` does not count:
    nobody has written down why.
    """
    fm = fm or {}
    if fm.get("superseded_by"):
        return True
    if str(fm.get("status") or "").strip().lower() != "stale":
        return False
    return bool(str(fm.get("stale_reason") or "").strip())


def _iter_catalog_entries(catalog_data: dict):
    """Yield every dict entry across all ``sections`` of the catalog."""
    for entries in (catalog_data.get("sections") or {}).values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict):
                yield entry


def _load_catalog(wiki_path: Path) -> dict | None:
    """Return the parsed ``_catalog.json`` for a wiki, or None on miss."""
    import json as _json

    cat = wiki_path / "_catalog.json"
    if not cat.exists():
        return None
    try:
        return _json.loads(cat.read_text(errors="replace"))
    except (OSError, _json.JSONDecodeError):
        return None


def count_pending_verdicts(
    wiki_path: Path,
    handle: str | None = None,
    *,
    soft_cap: int = 9,
) -> tuple[int, bool]:
    """Count the notes in ``wiki_path`` that still need a verdict.

    Backs the status-line chip. Counts the rows that
    :func:`list_pending_verdicts` returns, so the chip and the picker
    cannot disagree — a drift the two surfaces used to allow, because
    the picker dropped rows the chip had already counted.

    Pass ``handle`` so the count sees the same disagreements the picker
    does. Without a handle the sidecar stays unread, and a note whose
    stale verdict the user personally contradicted counts as settled.

    Returns ``(count, capped)`` where ``capped`` is True iff the soft
    cap fired. The status-line render uses ``"9+"`` instead of the
    raw count when ``capped`` is True.

    Cache miss (no catalog) returns ``(0, False)`` — the chip
    suppresses entirely until the next ``lore lint`` run.
    """
    # ponytail: counts the full list rather than early-exiting at the cap.
    # The candidate set is bounded by the catalog's stale markers plus the
    # orphan set, which is small in a healthy wiki. Restore an early exit
    # if a large orphan set ever makes the SessionStart chip measurably slow.
    entries = list_pending_verdicts(wiki_path, handle)
    if len(entries) > soft_cap:
        return soft_cap, True
    return len(entries), False


@dataclass(frozen=True)
class PendingEntry:
    """One picker-ready row for the ``/lore:verify`` resolver.

    Built by :func:`list_pending_verdicts` from the catalog walk plus a
    per-note frontmatter read + per-user sidecar lookup. Carries every
    field the picker needs so the skill never has to do additional I/O.
    """

    path: str  # wiki-relative
    slug: str
    cause: Literal["authored_marker", "orphan_broken"]
    reason: str
    confirmed_at: date | None
    disagreement: "Disagreement | None"  # forward ref; imported lazily
    stale_at: date | None  # for sort tiebreaking; None if not set
    mtime: date | None  # final fallback for sort


def _sort_key(entry: "PendingEntry") -> tuple:
    """Sort: disagreements first → authored_marker → orphan_broken;
    within bucket, most-recently-marked-stale first (then file mtime).

    Tuple ordering reverses dates (negate via ``date.toordinal``)
    so the natural ascending sort of tuples puts "more recent" first.
    """
    has_disagreement = entry.disagreement is not None
    cause_rank = 0 if entry.cause == "authored_marker" else 1
    stale_ord = -entry.stale_at.toordinal() if entry.stale_at else 0
    mtime_ord = -entry.mtime.toordinal() if entry.mtime else 0
    return (
        0 if has_disagreement else 1,
        cause_rank,
        stale_ord,
        mtime_ord,
        entry.path,
    )


def list_pending_verdicts(
    wiki_path: Path,
    handle: str | None = None,
) -> list[PendingEntry]:
    """Enumerate wiki-wide pending verdicts as picker-ready rows.

    The chip's coarse predicate (:func:`_is_pending_from_catalog_entry`)
    selects the set; per-note frontmatter reads then enrich each entry
    with the reason text + four-field stale schema (when present), and
    the per-user sidecar resolves the personal ``confirmed_at``.

    The returned list is sorted per the picker UX contract:

    * Disagreements first (someone marked stale, this user confirmed
      after — needs explicit resolution).
    * Then ``authored_marker`` (richer reason text from the note's
      own frontmatter) before ``orphan_broken`` (derived signal).
    * Within each bucket, most-recently-marked-stale first; falls back
      to file mtime; final tiebreak by path for stability.

    Args:
        wiki_path: Absolute path to the wiki root.
        handle: Current user's handle for personal-sidecar lookup.
            ``None`` or empty string means no sidecar read — every
            entry's ``confirmed_at`` will be ``None``. Solo vaults
            without ``_users.yml`` typically pass ``""``.

    Returns:
        List of :class:`PendingEntry`, possibly empty. Cache miss
        (missing/unreadable catalog) yields ``[]``.
    """
    from lore_core.disagreement import detect_disagreement
    from lore_core.schema import parse_frontmatter
    from lore_core.verdicts_sidecar import get_confirmed

    data = _load_catalog(wiki_path)
    if data is None:
        return []

    orphan_paths_str = set(data.get("orphan_set") or [])

    out: list[PendingEntry] = []
    for entry in _iter_catalog_entries(data):
        if not _is_pending_from_catalog_entry(entry, orphan_paths_str):
            continue

        rel_path = str(entry.get("path") or "")
        if not rel_path:
            continue
        target = wiki_path / rel_path
        try:
            target.resolve().relative_to(wiki_path.resolve())
        except ValueError:
            continue

        fm: dict = {}
        try:
            fm = parse_frontmatter(target.read_text(errors="replace"))
        except OSError:
            fm = {}

        # Determine cause: authored markers take precedence over orphan.
        authored = _has_authored_marker(fm)
        if authored is not None:
            key, value = authored
            cause: Literal["authored_marker", "orphan_broken"] = "authored_marker"
            reason = _format_marker_reason(key, value)
        elif rel_path in orphan_paths_str:
            cause = "orphan_broken"
            reason = "contains a broken wikilink"
        else:
            # Catalog flagged it but neither signal survived per-note
            # parsing (e.g. note's frontmatter was sanitised since
            # last lint). Skip rather than emit a no-cause entry.
            continue

        confirmed_at: date | None = None
        if handle:
            try:
                confirmed_at = get_confirmed(wiki_path, handle, rel_path)
            except (OSError, ValueError):
                confirmed_at = None

        disagreement = detect_disagreement(fm, confirmed_at)

        # Second stage: a recorded verdict answers the question the list
        # asks, so the note leaves the worklist. A disagreement reopens
        # it — the personal confirm contradicts the stale verdict.
        if disagreement is None and _verdict_recorded(fm):
            continue

        stale_at: date | None = None
        raw_stale_at = fm.get("stale_at")
        if isinstance(raw_stale_at, date):
            stale_at = raw_stale_at
        elif isinstance(raw_stale_at, str):
            try:
                stale_at = date.fromisoformat(raw_stale_at)
            except ValueError:
                stale_at = None

        mtime = _file_mtime_date(target)

        slug = Path(rel_path).stem

        out.append(
            PendingEntry(
                path=rel_path,
                slug=slug,
                cause=cause,
                reason=reason,
                confirmed_at=confirmed_at,
                disagreement=disagreement,
                stale_at=stale_at,
                mtime=mtime,
            )
        )

    out.sort(key=_sort_key)
    return out


def pending_entry_to_dict(entry: PendingEntry) -> dict:
    """JSON-friendly render for the MCP ``pending`` array."""
    from lore_core.disagreement import disagreement_to_dict

    return {
        "path": entry.path,
        "slug": entry.slug,
        "cause": entry.cause,
        "reason": entry.reason,
        "confirmed_at": entry.confirmed_at.isoformat() if entry.confirmed_at else None,
        "disagreement": (
            disagreement_to_dict(entry.disagreement) if entry.disagreement else None
        ),
    }


def load_orphan_set(wiki_path: Path) -> set[Path]:
    """Load the cached orphan set for a wiki.

    Slice 4 of PRD #65: ``lore lint`` writes a list of wiki-relative
    paths under ``_catalog.json`` → ``orphan_set``. Computing orphans
    on every retrieval would be too expensive (rglob over the wiki +
    one frontmatter parse per note). The cache is refreshed on each
    lint run; eventual-consistency is acceptable per the PRD — the
    worst case is a brief window where a fresh-but-not-yet-linted
    note is incorrectly flagged or unflagged.

    Returns absolute paths so callers can use plain ``in`` membership
    against the same paths they pass to :func:`compute_freshness`.

    Cache miss (no catalog, no ``orphan_set`` key, malformed JSON)
    degrades gracefully to an empty set — the freshness signal stays
    ``confirmed`` for any note that would otherwise have been
    orphan-flagged, with no error.
    """
    import json as _json

    cat = wiki_path / "_catalog.json"
    if not cat.exists():
        return set()
    try:
        data = _json.loads(cat.read_text(errors="replace"))
    except (OSError, _json.JSONDecodeError):
        return set()
    raw = data.get("orphan_set")
    if not isinstance(raw, list):
        return set()
    out: set[Path] = set()
    for rel in raw:
        if not isinstance(rel, str) or not rel:
            continue
        out.add((wiki_path / rel).resolve())
    return out
