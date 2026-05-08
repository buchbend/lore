"""Tests for ``lore_core.freshness`` — slice 1 of PRD #65.

These cover the pure classification logic. Personal-confirm suppression
behavior is exercised here too because the parameter shape is part of
slice 1's signature, even though slice 1 always passes
``sidecar_confirmed_at=None`` from the retrieval surfaces. The full
behaviour is wired in slice 6 — these tests pin the contract early so
slice 6's wiring is the only change needed.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from lore_core.freshness import (
    PERSONAL_CONFIRM_RECENCY_DAYS,
    FreshnessSignal,
    compute_freshness,
    signal_to_dict,
)


def _write_note(path: Path, body: str = "body") -> None:
    path.write_text(body)


def _set_mtime(path: Path, target: date) -> None:
    """Set the mtime of ``path`` to noon on ``target``."""
    import os
    import time

    ts = time.mktime((target.year, target.month, target.day, 12, 0, 0, 0, 0, -1))
    os.utime(path, (ts, ts))


# ---------------------------------------------------------------------------
# Authored-marker variants — each in isolation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fm, expected_reason_substr",
    [
        ({"status": "stale"}, "marked stale"),
        ({"superseded_by": "[[newer]]"}, "superseded by"),
        ({"supersede_candidate": "[[newer]]"}, "supersede candidate:"),
        ({"supersede_candidate_of": "[[older]]"}, "supersede candidate of"),
    ],
)
def test_each_authored_marker_flags_stale_candidate(
    tmp_path: Path, fm: dict, expected_reason_substr: str
) -> None:
    note = tmp_path / "n.md"
    _write_note(note)
    sig = compute_freshness(fm, note, tmp_path, None, set())
    assert sig.status == "stale-candidate"
    assert sig.cause == "authored_marker"
    assert sig.reason is not None
    assert expected_reason_substr in sig.reason


def test_no_markers_returns_confirmed(tmp_path: Path) -> None:
    note = tmp_path / "n.md"
    _write_note(note)
    sig = compute_freshness({}, note, tmp_path, None, set())
    assert sig == FreshnessSignal(
        status="confirmed", cause="none", reason=None, confirmed_at=None
    )


def test_only_unrelated_fields_stay_confirmed(tmp_path: Path) -> None:
    note = tmp_path / "n.md"
    _write_note(note)
    sig = compute_freshness(
        {"type": "concept", "tags": ["x"], "created": "2026-01-01"},
        note,
        tmp_path,
        None,
        set(),
    )
    assert sig.status == "confirmed"


def test_status_active_does_not_flag(tmp_path: Path) -> None:
    note = tmp_path / "n.md"
    _write_note(note)
    sig = compute_freshness({"status": "active"}, note, tmp_path, None, set())
    assert sig.status == "confirmed"


# ---------------------------------------------------------------------------
# Multiple markers + precedence
# ---------------------------------------------------------------------------


def test_multiple_markers_picks_first_hard(tmp_path: Path) -> None:
    note = tmp_path / "n.md"
    _write_note(note)
    fm = {"status": "stale", "supersede_candidate": "[[other]]"}
    sig = compute_freshness(fm, note, tmp_path, None, set())
    # Hard marker (status) wins over soft.
    assert sig.cause == "authored_marker"
    assert "marked stale" in (sig.reason or "")


def test_authored_marker_wins_over_orphan(tmp_path: Path) -> None:
    note = tmp_path / "n.md"
    _write_note(note)
    fm = {"status": "stale"}
    sig = compute_freshness(fm, note, tmp_path, None, {note})
    assert sig.cause == "authored_marker"  # docstring contract


def test_orphan_only_yields_orphan_broken(tmp_path: Path) -> None:
    note = tmp_path / "n.md"
    _write_note(note)
    sig = compute_freshness({}, note, tmp_path, None, {note})
    assert sig.status == "stale-candidate"
    assert sig.cause == "orphan_broken"
    assert sig.reason is not None


def test_path_outside_orphan_set_stays_confirmed(tmp_path: Path) -> None:
    note = tmp_path / "n.md"
    other = tmp_path / "other.md"
    _write_note(note)
    _write_note(other)
    sig = compute_freshness({}, note, tmp_path, None, {other})
    assert sig.status == "confirmed"


# ---------------------------------------------------------------------------
# Age never flags
# ---------------------------------------------------------------------------


def test_age_alone_never_flags(tmp_path: Path) -> None:
    """A note can be ancient — no markers, no orphan → confirmed."""
    note = tmp_path / "n.md"
    _write_note(note)
    fm = {"last_reviewed": "2010-01-01", "created": "2010-01-01"}
    sig = compute_freshness(fm, note, tmp_path, None, set())
    assert sig.status == "confirmed"


def test_missing_last_confirmed_does_not_flag(tmp_path: Path) -> None:
    note = tmp_path / "n.md"
    _write_note(note)
    sig = compute_freshness({"type": "concept"}, note, tmp_path, None, set())
    assert sig.status == "confirmed"


# ---------------------------------------------------------------------------
# Personal confirm suppression (slice 6 contract pinned in slice 1)
# ---------------------------------------------------------------------------


def test_personal_confirm_suppresses_soft_marker(tmp_path: Path) -> None:
    note = tmp_path / "n.md"
    _write_note(note)
    today = date(2026, 5, 8)
    # Note was last edited a week ago; confirmed two days ago — confirm
    # is newer than mtime, so the soft marker is suppressed.
    _set_mtime(note, today - timedelta(days=7))
    fm = {"supersede_candidate": "[[other]]"}
    confirmed_at = today - timedelta(days=2)
    sig = compute_freshness(
        fm, note, tmp_path, confirmed_at, set(), today=today
    )
    assert sig.status == "confirmed"
    assert sig.confirmed_at == confirmed_at


def test_personal_confirm_does_not_suppress_status_stale(tmp_path: Path) -> None:
    note = tmp_path / "n.md"
    _write_note(note)
    fm = {"status": "stale"}
    today = date(2026, 5, 8)
    confirmed_at = today - timedelta(days=2)
    sig = compute_freshness(
        fm, note, tmp_path, confirmed_at, set(), today=today
    )
    # Hard marker wins — confirm cannot vouch for team-wide truth.
    assert sig.status == "stale-candidate"
    assert sig.cause == "authored_marker"


def test_personal_confirm_does_not_suppress_superseded_by(tmp_path: Path) -> None:
    note = tmp_path / "n.md"
    _write_note(note)
    fm = {"superseded_by": "[[newer]]"}
    today = date(2026, 5, 8)
    confirmed_at = today - timedelta(days=2)
    sig = compute_freshness(
        fm, note, tmp_path, confirmed_at, set(), today=today
    )
    assert sig.status == "stale-candidate"


def test_personal_confirm_outside_recency_window_does_not_suppress(
    tmp_path: Path,
) -> None:
    note = tmp_path / "n.md"
    _write_note(note)
    today = date(2026, 5, 8)
    # Note edited long ago, but the confirm is also older than the window.
    confirmed_at = today - timedelta(days=PERSONAL_CONFIRM_RECENCY_DAYS + 5)
    _set_mtime(note, confirmed_at - timedelta(days=2))
    fm = {"supersede_candidate": "[[other]]"}
    sig = compute_freshness(
        fm, note, tmp_path, confirmed_at, set(), today=today
    )
    assert sig.status == "stale-candidate"


def test_personal_confirm_after_edit_does_not_suppress(tmp_path: Path) -> None:
    """Edit-then-confirm-stale: mtime-after-confirm invalidates suppression."""
    note = tmp_path / "n.md"
    _write_note(note)
    today = date(2026, 5, 8)
    # Confirm last week, but the note was edited today (after the confirm).
    confirmed_at = today - timedelta(days=7)
    # Bump mtime to today (later than confirmed_at).
    import os
    import time

    target_ts = time.mktime((today.year, today.month, today.day, 12, 0, 0, 0, 0, -1))
    os.utime(note, (target_ts, target_ts))

    fm = {"supersede_candidate": "[[other]]"}
    sig = compute_freshness(
        fm, note, tmp_path, confirmed_at, set(), today=today
    )
    assert sig.status == "stale-candidate"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_frontmatter_dict(tmp_path: Path) -> None:
    note = tmp_path / "n.md"
    _write_note(note)
    sig = compute_freshness({}, note, tmp_path, None, set())
    assert sig.status == "confirmed"


def test_none_frontmatter_treated_as_empty(tmp_path: Path) -> None:
    note = tmp_path / "n.md"
    _write_note(note)
    sig = compute_freshness(None, note, tmp_path, None, set())  # type: ignore[arg-type]
    assert sig.status == "confirmed"


def test_orphan_set_none_treated_as_empty(tmp_path: Path) -> None:
    note = tmp_path / "n.md"
    _write_note(note)
    sig = compute_freshness({}, note, tmp_path, None, None)
    assert sig.status == "confirmed"


def test_signal_to_dict_shape(tmp_path: Path) -> None:
    note = tmp_path / "n.md"
    _write_note(note)
    sig = compute_freshness({"status": "stale"}, note, tmp_path, None, set())
    d = signal_to_dict(sig)
    assert d == {
        "status": "stale-candidate",
        "cause": "authored_marker",
        "reason": "marked stale",
        "confirmed_at": None,
    }


def test_signal_to_dict_with_confirmed_at(tmp_path: Path) -> None:
    note = tmp_path / "n.md"
    _write_note(note)
    today = date(2026, 5, 8)
    confirmed = today - timedelta(days=1)
    _set_mtime(note, today - timedelta(days=5))
    sig = compute_freshness(
        {"supersede_candidate": "[[x]]"},
        note,
        tmp_path,
        confirmed,
        set(),
        today=today,
    )
    d = signal_to_dict(sig)
    assert d["status"] == "confirmed"
    assert d["confirmed_at"] == confirmed.isoformat()
