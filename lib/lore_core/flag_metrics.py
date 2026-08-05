"""Read-side flag measurement — aggregating `flag.py`'s spine events (#360).

Under-flagging is the flag architecture's main failure mode (PRD 0011)
and is invisible without measurement: the flag is the crossing from a
private session to the team wiki, and the only one left once the
only crossing left, so a fact an agent should have
flagged and didn't leaves no trace anywhere a human would look. This
module reads what `flag.py` already emits — nothing here writes, and
nothing here duplicates the pending scan (`flag.count_pending` stays the
one source of pending state, ADR 0008).

Backs `lore status`'s per-wiki flag counters and `lore trace`'s flag
filter.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
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

    Reads both the live spine and its rotated cold sibling
    (``spine.jsonl.1``), unlike a plain ``read_spine`` call. A known-gem
    baseline campaign or a flag's review can outlive one rotation
    (``retention.hot_days``, default 7) — the janitor rotates the hot
    spine opportunistically from the hook path, so this is live
    behaviour, not a hypothetical. Reading the cold file too widens the
    counted window to ``retention.cold_days`` (default 30) instead of
    silently resetting mid-campaign. Still bounded: the janitor deletes
    cold records past that, so a campaign or an outstanding review
    longer than that window is not covered — see
    ``docs/how-to/measure-flag-quality.md``.

    Sorted by ``ts`` — the spine is append-only but not guaranteed
    strictly time-ordered (rotation, clock skew), the same caveat every
    other spine reader carries.
    """
    cold_path = lore_root / ".lore" / "spine.jsonl.1"
    records = read_spine(lore_root, source=SPINE_SOURCE, path=cold_path)
    records += read_spine(lore_root, source=SPINE_SOURCE)
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
    """Parse an ISO timestamp, normalizing a naive one to UTC.

    A naive and an aware ``datetime`` can't be subtracted, so a caller
    diffing two parsed timestamps would crash on a naive one even though
    every value this module produces is well-formed — a hand-edited or
    otherwise malformed record must degrade this function's contract
    (``None``), not raise past it. Same idiom as
    ``spine._oldest_record_age_days`` / ``status_cmd._resolve_now``.
    """
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def review_latency_seconds(events: list[dict[str, Any]], flag_id: str) -> float | None:
    """Seconds between one flag's write and its first RESOLVING verdict.

    ``None`` when the flag has no write event, no resolving verdict yet
    (still pending, possibly after one or more retargets), or a
    timestamp fails to parse. A retarget does not resolve review —
    ``flag.retarget`` keeps the block's unreviewed marker and leaves it
    in the walk — so a retarget verdict is skipped when picking the
    review timestamp; only the first accept/decline counts. This mirrors
    ``flag_counts``'s own treatment of ``retargeted`` as distinct from an
    accept/decline outcome: write 10:00 -> retarget 10:01 -> accept
    12:00 must report the write-to-accept gap, not write-to-retarget.

    Takes ``events`` — the same list `flag_events` returns — rather than
    reading the spine itself, so a caller filtering to one wiki gets a
    latency computed over exactly what it already read.
    """
    write_ts = review_ts = None
    for rec in events:
        data = rec.get("data") or {}
        if data.get("flag_id") != flag_id:
            continue
        event = rec.get("event")
        if event == EV_WRITE and write_ts is None:
            write_ts = rec.get("ts")
        elif event == EV_REVIEW and review_ts is None and data.get("verdict") != "retarget":
            review_ts = rec.get("ts")
    if write_ts is None or review_ts is None:
        return None
    a, b = _parse_ts(write_ts), _parse_ts(review_ts)
    if a is None or b is None:
        return None
    return (b - a).total_seconds()
