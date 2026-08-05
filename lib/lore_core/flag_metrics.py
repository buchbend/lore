"""Read-side flag measurement — aggregating `flag.py`'s spine events (#360).

Under-flagging is the flag architecture's main failure mode (PRD 0010)
and is invisible without measurement: the flag is the only crossing from
a private session to the team wiki, so a fact an agent should have
flagged and didn't leaves no trace anywhere a human would look. This
module reads what `flag.py` already emits — nothing here writes, and
nothing here duplicates the pending scan (`flag.count_pending` stays the
one source of pending state, ADR 0008).

Backs `lore status`'s per-wiki flag counters and `lore trace`'s flag
filter.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from lore_core.flag import EV_REVIEW, EV_WRITE, SPINE_SOURCE, count_pending
from lore_core.spine import read_spine


@dataclass(frozen=True)
class FlagCounts:
    """Per-wiki flag counters for `lore status`."""

    written: int
    withheld: int
    pending: int
    accepted: int
    declined: int
    retargeted: int


def flag_events(lore_root: Path, *, wiki: str | None = None) -> list[dict[str, Any]]:
    """Every flag-write/flag-review spine record, chronological.

    Sorted by ``ts`` — the spine is append-only but not guaranteed
    strictly time-ordered (rotation, clock skew), the same caveat every
    other spine reader carries.
    """
    records = read_spine(lore_root, source=SPINE_SOURCE)
    if wiki is not None:
        records = [r for r in records if r.get("wiki") == wiki]
    return sorted(records, key=lambda r: r.get("ts", ""))


def flag_counts(lore_root: Path, wiki: str) -> FlagCounts:
    """Aggregate flag counters for one wiki.

    ``written``/``withheld`` split the flag-write outcome apart: a
    withheld flag never reaches the note (its payload carries no flag
    text, by design), so folding it into "written" would hide the
    withhold rate the flag slice deliberately made measurable.

    ``pending`` is never replayed from events — it is
    ``flag.count_pending``, the one derivation ADR 0008 allows (a note
    scan, not an index), so a note a human edited by hand still counts
    correctly.

    ``retargeted`` stays apart from accepted/declined: a retarget moves
    a flag to a better home, it does not resolve the review — the flag
    keeps its unreviewed marker and stays pending afterward.
    """
    written = withheld = accepted = declined = retargeted = 0
    for rec in flag_events(lore_root, wiki=wiki):
        data = rec.get("data") or {}
        event = rec.get("event")
        if event == EV_WRITE:
            outcome = data.get("outcome")
            if outcome == "written":
                written += 1
            elif outcome == "withheld":
                withheld += 1
        elif event == EV_REVIEW:
            verdict = data.get("verdict")
            if verdict == "accept":
                accepted += 1
            elif verdict == "decline":
                declined += 1
            elif verdict == "retarget":
                retargeted += 1
    pending = count_pending(lore_root / "wiki" / wiki)
    return FlagCounts(
        written=written,
        withheld=withheld,
        pending=pending,
        accepted=accepted,
        declined=declined,
        retargeted=retargeted,
    )


def _parse_ts(raw: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def review_latency_seconds(events: list[dict[str, Any]], flag_id: str) -> float | None:
    """Seconds between one flag's write and its review verdict.

    ``None`` when the flag has no write event, no verdict yet (still
    pending), or either timestamp fails to parse. Takes ``events`` — the
    same list `flag_events` returns — rather than reading the spine
    itself, so a caller filtering to one wiki or one time window gets a
    latency computed over exactly what it already read.
    """
    write_ts = review_ts = None
    for rec in events:
        if (rec.get("data") or {}).get("flag_id") != flag_id:
            continue
        if rec.get("event") == EV_WRITE and write_ts is None:
            write_ts = rec.get("ts")
        elif rec.get("event") == EV_REVIEW and review_ts is None:
            review_ts = rec.get("ts")
    if write_ts is None or review_ts is None:
        return None
    a, b = _parse_ts(write_ts), _parse_ts(review_ts)
    if a is None or b is None:
        return None
    return (b - a).total_seconds()
