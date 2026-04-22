"""Tests for the canonical relative_time / relative_day formatters.

- Seconds-granular "X ago" → relative_time
- Day-granular "today / yesterday / 3d ago" → relative_day
- Future timestamps → "just now" (clock-skew robust; pinned here and
  in the module docstring so it doesn't drift)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from lore_core.timefmt import relative_day, relative_time


_NOW = datetime(2026, 4, 21, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# relative_time
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "delta_seconds,expected",
    [
        (0, "just now"),
        (30, "just now"),
        (59, "just now"),
        (60, "1m ago"),
        (61, "1m ago"),
        (3599, "59m ago"),
        (3600, "1h ago"),
        (3661, "1h ago"),
        (86399, "23h ago"),
        (86400, "yesterday"),
        (86400 * 2, "2d ago"),
        (86400 * 7, "1w ago"),
        (86400 * 14, "2w ago"),
    ],
)
def test_relative_time_all_bucket_transitions(delta_seconds: float, expected: str) -> None:
    # NOTE: 86400s → "1d ago" here, but 86400s + 1s → "yesterday"; see
    # test_relative_time_yesterday_bucket for the copy-friendly bucket.
    ts = _NOW - timedelta(seconds=delta_seconds)
    assert relative_time(ts, now=_NOW) == expected


@pytest.mark.parametrize(
    "delta_seconds,expected",
    [
        # 1d + small offset → "yesterday" (user-friendly copy bucket)
        (86400 + 1, "yesterday"),
        (86400 + 3600, "yesterday"),
        (86400 * 2 - 1, "yesterday"),
    ],
)
def test_relative_time_yesterday_bucket(delta_seconds: float, expected: str) -> None:
    ts = _NOW - timedelta(seconds=delta_seconds)
    assert relative_time(ts, now=_NOW) == expected


def test_relative_time_handles_naive_datetime() -> None:
    """Naive datetimes are assumed UTC."""
    naive = (_NOW - timedelta(minutes=30)).replace(tzinfo=None)
    assert relative_time(naive, now=_NOW) == "30m ago"


def test_relative_time_handles_z_suffix_iso_string() -> None:
    """`2026-04-21T11:00:00Z` must parse cleanly."""
    iso = (_NOW - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    assert relative_time(iso, now=_NOW) == "1h ago"


def test_relative_time_handles_iso_string_with_offset() -> None:
    iso = (_NOW - timedelta(hours=2)).isoformat()
    assert relative_time(iso, now=_NOW) == "2h ago"


def test_relative_time_none_returns_question_mark() -> None:
    assert relative_time(None, now=_NOW) == "?"


def test_relative_time_empty_string_returns_question_mark() -> None:
    assert relative_time("", now=_NOW) == "?"


def test_relative_time_unparseable_string_returns_as_is() -> None:
    """Garbage input should not crash — return the input so the caller sees it."""
    assert relative_time("not-a-date", now=_NOW) == "not-a-date"


def test_relative_time_future_timestamp_is_just_now() -> None:
    """Clock-skew canonical: 5m in the future renders as 'just now', not '-5m ago'."""
    future = _NOW + timedelta(minutes=5)
    assert relative_time(future, now=_NOW) == "just now"


def test_relative_time_short_mode_omits_ago() -> None:
    ts = _NOW - timedelta(minutes=5)
    assert relative_time(ts, now=_NOW, short=True) == "5m"


def test_relative_time_short_mode_all_buckets() -> None:
    ts_table = [
        (30, "now"),       # short for "just now"
        (60 * 5, "5m"),
        (3600 * 2, "2h"),
        (86400 * 3, "3d"),
        (86400 * 14, "2w"),
    ]
    for secs, expected in ts_table:
        assert relative_time(_NOW - timedelta(seconds=secs), now=_NOW, short=True) == expected


def test_relative_time_default_now_uses_utcnow() -> None:
    """When `now` is omitted, the function uses datetime.now(UTC)."""
    ts = datetime.now(UTC) - timedelta(minutes=3)
    result = relative_time(ts)
    assert result in ("2m ago", "3m ago", "4m ago"), (
        f"expected a few-minutes-ago rendering, got {result!r}"
    )


# ---------------------------------------------------------------------------
# relative_day
# ---------------------------------------------------------------------------


def test_relative_day_today() -> None:
    ts = _NOW.replace(hour=3)  # same day, different hour
    assert relative_day(ts, now=_NOW) == "today"


def test_relative_day_yesterday() -> None:
    ts = _NOW - timedelta(days=1)
    assert relative_day(ts, now=_NOW) == "yesterday"


def test_relative_day_within_week() -> None:
    ts = _NOW - timedelta(days=3)
    assert relative_day(ts, now=_NOW) == "3d ago"


def test_relative_day_older_falls_back_to_relative_time() -> None:
    ts = _NOW - timedelta(days=14)
    assert relative_day(ts, now=_NOW) == "2w ago"


def test_relative_day_handles_none() -> None:
    assert relative_day(None, now=_NOW) == "?"


# ---------------------------------------------------------------------------
# Grep acceptance guard
# ---------------------------------------------------------------------------


def test_no_relative_time_duplicates_remain() -> None:
    """After Task 6, only timefmt.py defines relative/rel_* time helpers."""
    import re
    import subprocess
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [
            "grep", "-rEn",
            r"def (_)?(relative|rel)_(time|cap|ago)",
            str(repo / "lib"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    # Only timefmt.py definitions should remain.
    offenders = [
        line for line in result.stdout.splitlines()
        if "timefmt.py" not in line
    ]
    assert not offenders, (
        "Expected all relative-time helpers inside lore_core/timefmt.py; "
        f"found duplicates:\n{chr(10).join(offenders)}"
    )
