"""Tests for ``lore_core.disagreement`` — slice 9 of PRD #65."""

from __future__ import annotations

from datetime import date

import pytest

from lore_core.disagreement import (
    Disagreement,
    detect_disagreement,
    disagreement_to_dict,
)


def _stale_fm(stale_at, by="alice", reason="X"):
    return {
        "status": "stale",
        "stale_by": by,
        "stale_at": stale_at,
        "stale_reason": reason,
    }


def test_detect_when_confirm_after_stale() -> None:
    fm = _stale_fm(date(2026, 5, 1))
    out = detect_disagreement(fm, sidecar_confirmed_at=date(2026, 5, 5))
    assert isinstance(out, Disagreement)
    assert out.stale_by == "alice"
    assert out.stale_at == date(2026, 5, 1)
    assert out.self_confirmed_at == date(2026, 5, 5)
    assert out.stale_reason == "X"


def test_detect_when_confirm_before_stale_returns_none() -> None:
    fm = _stale_fm(date(2026, 5, 5))
    out = detect_disagreement(fm, sidecar_confirmed_at=date(2026, 5, 1))
    assert out is None


def test_detect_returns_none_without_status_stale() -> None:
    out = detect_disagreement(
        {"status": "active"}, sidecar_confirmed_at=date(2026, 5, 5)
    )
    assert out is None


def test_detect_returns_none_without_personal_confirm() -> None:
    fm = _stale_fm(date(2026, 5, 1))
    assert detect_disagreement(fm, None) is None


def test_detect_graceful_when_stale_at_missing() -> None:
    fm = {"status": "stale", "stale_by": "alice", "stale_reason": "X"}
    assert detect_disagreement(fm, date(2026, 5, 5)) is None


def test_detect_handles_iso_string_dates() -> None:
    fm = _stale_fm("2026-05-01")
    out = detect_disagreement(fm, date(2026, 5, 5))
    assert out is not None
    assert out.stale_at == date(2026, 5, 1)


def test_detect_handles_naive_date_object() -> None:
    fm = _stale_fm(date(2026, 5, 1))
    out = detect_disagreement(fm, date(2026, 5, 5))
    assert out is not None


def test_detect_same_user_changing_mind() -> None:
    """Solo-user vault: marked stale weeks ago, forgot, confirmed today."""
    fm = _stale_fm(date(2026, 4, 1))
    out = detect_disagreement(fm, date(2026, 5, 5))
    assert out is not None


def test_detect_equal_dates_not_disagreement() -> None:
    fm = _stale_fm(date(2026, 5, 5))
    assert detect_disagreement(fm, date(2026, 5, 5)) is None


def test_to_dict_shape() -> None:
    fm = _stale_fm(date(2026, 5, 1))
    out = detect_disagreement(fm, date(2026, 5, 5))
    assert out is not None
    d = disagreement_to_dict(out)
    assert d == {
        "stale_by": "alice",
        "stale_at": "2026-05-01",
        "stale_reason": "X",
        "self_confirmed_at": "2026-05-05",
    }


def test_detect_unparseable_stale_at_string() -> None:
    fm = {"status": "stale", "stale_at": "not-a-date"}
    assert detect_disagreement(fm, date(2026, 5, 5)) is None


def test_detect_status_stale_case_insensitive() -> None:
    fm = {"status": "Stale", "stale_at": date(2026, 5, 1)}
    out = detect_disagreement(fm, date(2026, 5, 5))
    assert out is not None
