"""Flush lifecycle state machine tests (issue #189).

The transition table (legal AND illegal) and the bounded-retry / dead-letter
scheduling are the heart of the slice — a stuck flush must be a queryable
row, never an absence of evidence.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from lore_core.flush_store import (
    MAX_ATTEMPTS,
    RETRY_CAP_SECONDS,
    FlushRecord,
    FlushState,
    FlushStore,
    FlushTransitionError,
    backoff_seconds,
    is_legal_transition,
    is_retry_due,
)
from lore_core.spine import ErrorCode, read_spine, validate_envelope


def _spine(lore_root: Path) -> list[dict]:
    return read_spine(lore_root, source="curator")


# ---------------------------------------------------------------------------
# AC1 — transition table (legal AND illegal)
# ---------------------------------------------------------------------------

_LEGAL_PAIRS = [
    (FlushState.QUEUED, FlushState.RUNNING),
    (FlushState.RUNNING, FlushState.PUBLISHED),
    (FlushState.RUNNING, FlushState.WITHHELD),
    (FlushState.RUNNING, FlushState.DEAD_LETTERED),
    (FlushState.RUNNING, FlushState.QUEUED),  # scheduled retry
]

# Every ordered pair not in the legal set must be illegal — including
# self-loops and any transition out of a terminal state.
_ALL_STATES = list(FlushState)
_ILLEGAL_PAIRS = [(a, b) for a in _ALL_STATES for b in _ALL_STATES if (a, b) not in _LEGAL_PAIRS]


@pytest.mark.parametrize("frm,to", _LEGAL_PAIRS)
def test_legal_transitions_accepted(frm, to):
    assert is_legal_transition(frm, to) is True


@pytest.mark.parametrize("frm,to", _ILLEGAL_PAIRS)
def test_illegal_transitions_rejected(frm, to):
    assert is_legal_transition(frm, to) is False


def test_store_transition_raises_on_illegal(tmp_path: Path):
    store = FlushStore(tmp_path)
    rec = store.begin("t1__20260101", wiki="private")
    store.transition(rec, FlushState.RUNNING)
    store.transition(rec, FlushState.PUBLISHED)
    # published is terminal — any further transition is illegal.
    with pytest.raises(FlushTransitionError):
        store.transition(rec, FlushState.RUNNING)


def test_store_transition_happy_path_persists(tmp_path: Path):
    store = FlushStore(tmp_path)
    rec = store.begin("t1__20260101", wiki="private")
    assert rec.state == FlushState.QUEUED.value
    store.transition(rec, FlushState.RUNNING)
    store.transition(rec, FlushState.PUBLISHED)
    reloaded = store.get(rec.flush_id)
    assert reloaded is not None
    assert reloaded.state == FlushState.PUBLISHED.value
    assert reloaded.is_terminal


# ---------------------------------------------------------------------------
# AC1 — the record shape: attempt counter + next-eligible-retry timestamp
# ---------------------------------------------------------------------------


def test_record_has_attempt_counter_and_next_retry(tmp_path: Path):
    store = FlushStore(tmp_path)
    rec = store.begin("t1__20260101")
    assert rec.attempts == 0
    assert rec.next_retry_at is None
    assert rec.trace_id is None  # #188 fills this


def test_persisted_json_round_trips(tmp_path: Path):
    store = FlushStore(tmp_path)
    rec = store.begin("t1__20260101", wiki="private")
    raw = json.loads((tmp_path / ".lore" / "flushes" / f"{rec.flush_id}.json").read_text())
    assert raw["buffer_stem"] == "t1__20260101"
    assert raw["state"] == "queued"
    assert raw["attempts"] == 0


# ---------------------------------------------------------------------------
# AC2 — bounded retries with backoff; exhaustion -> dead-letter (structured)
# ---------------------------------------------------------------------------


def test_backoff_is_exponential_and_capped():
    assert backoff_seconds(1) == 60
    assert backoff_seconds(2) == 120
    assert backoff_seconds(3) == 240
    # Never exceeds the cap however large the attempt.
    assert backoff_seconds(999) == RETRY_CAP_SECONDS


def test_record_failure_schedules_retry_before_exhaustion(tmp_path: Path):
    store = FlushStore(tmp_path)
    rec = store.begin("t1__20260101")
    store.transition(rec, FlushState.RUNNING)
    rec = store.record_failure(rec, error_code=ErrorCode.COMPOSE_FAILED)
    # First failure: retry scheduled, not dead-lettered.
    assert rec.state == FlushState.QUEUED.value
    assert rec.attempts == 1
    assert rec.next_retry_at is not None
    assert rec.reason is None


def test_record_failure_dead_letters_on_exhaustion(tmp_path: Path):
    store = FlushStore(tmp_path)
    rec = store.begin("t1__20260101")
    for _ in range(MAX_ATTEMPTS):
        store.transition(rec, FlushState.RUNNING)
        rec = store.record_failure(rec, error_code=ErrorCode.COMPOSE_FAILED)
    assert rec.state == FlushState.DEAD_LETTERED.value
    assert rec.attempts == MAX_ATTEMPTS
    # Structured reason from the closed enum — never a free-form string.
    assert rec.reason == ErrorCode.COMPOSE_FAILED.value
    assert rec.reason in {c.value for c in ErrorCode}


def test_dead_letter_reason_must_be_enum(tmp_path: Path):
    store = FlushStore(tmp_path)
    rec = store.begin("t1__20260101")
    store.transition(rec, FlushState.RUNNING)
    with pytest.raises(ValueError):
        store.transition(rec, FlushState.DEAD_LETTERED, reason="free-form-string")


# ---------------------------------------------------------------------------
# AC1/AC2 — every transition emits a well-formed spine event
# ---------------------------------------------------------------------------


def test_transitions_emit_spine_events(tmp_path: Path):
    store = FlushStore(tmp_path)
    rec = store.begin("t1__20260101", wiki="private")
    store.transition(rec, FlushState.RUNNING)
    store.transition(rec, FlushState.WITHHELD)
    events = [r["event"] for r in _spine(tmp_path)]
    assert "flush-queued" in events
    assert "flush-running" in events
    assert "flush-withheld" in events
    for r in _spine(tmp_path):
        validate_envelope(r)  # closed source/level/error_code sets


def test_dead_letter_event_carries_error_code(tmp_path: Path):
    store = FlushStore(tmp_path)
    rec = store.begin("t1__20260101")
    for _ in range(MAX_ATTEMPTS):
        store.transition(rec, FlushState.RUNNING)
        rec = store.record_failure(rec, error_code=ErrorCode.COMPOSE_FAILED)
    dl = [r for r in _spine(tmp_path) if r["event"] == "flush-dead-lettered"]
    assert dl, "a dead-letter must emit an error-level spine event"
    assert dl[-1]["level"] == "error"
    assert dl[-1]["error_code"] == ErrorCode.COMPOSE_FAILED.value


# ---------------------------------------------------------------------------
# AC5 — queued / running / dead-lettered flushes are listable
# ---------------------------------------------------------------------------


def test_list_by_state(tmp_path: Path):
    store = FlushStore(tmp_path)
    store.begin("q__20260101")  # stays queued
    r = store.begin("r__20260101")
    store.transition(r, FlushState.RUNNING)
    d = store.begin("d__20260101")
    for _ in range(MAX_ATTEMPTS):
        store.transition(d, FlushState.RUNNING)
        d = store.record_failure(d, error_code=ErrorCode.SPAWN_FAILED)

    assert {x.buffer_stem for x in store.list(state=FlushState.QUEUED)} == {"q__20260101"}
    assert {x.buffer_stem for x in store.list(state=FlushState.RUNNING)} == {"r__20260101"}
    assert {x.buffer_stem for x in store.list(state=FlushState.DEAD_LETTERED)} == {"d__20260101"}
    # No filter -> all records.
    assert len(store.list()) == 3


# ---------------------------------------------------------------------------
# begin() lifecycle: idempotent while active, reopens after terminal
# ---------------------------------------------------------------------------


def test_begin_returns_active_record_preserving_attempts(tmp_path: Path):
    store = FlushStore(tmp_path)
    rec = store.begin("t1__20260101")
    store.transition(rec, FlushState.RUNNING)
    rec = store.record_failure(rec, error_code=ErrorCode.COMPOSE_FAILED)  # attempts=1, queued
    again = store.begin("t1__20260101")
    assert again.flush_id == rec.flush_id
    assert again.attempts == 1  # not reset while still active


def test_begin_reopens_after_terminal(tmp_path: Path):
    store = FlushStore(tmp_path)
    rec = store.begin("t1__20260101")
    store.transition(rec, FlushState.RUNNING)
    store.transition(rec, FlushState.PUBLISHED)
    reopened = store.begin("t1__20260101")
    assert reopened.state == FlushState.QUEUED.value
    assert reopened.attempts == 0  # fresh unit


# ---------------------------------------------------------------------------
# next-eligible-retry gating
# ---------------------------------------------------------------------------


def test_is_retry_due(tmp_path: Path):
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    future = (now + timedelta(seconds=120)).isoformat().replace("+00:00", "Z")
    past = (now - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    queued_future = FlushRecord(flush_id="x", buffer_stem="x", state="queued", next_retry_at=future)
    queued_past = FlushRecord(flush_id="x", buffer_stem="x", state="queued", next_retry_at=past)
    running = FlushRecord(flush_id="x", buffer_stem="x", state="running")
    assert is_retry_due(queued_future, now=now) is False
    assert is_retry_due(queued_past, now=now) is True
    assert is_retry_due(running, now=now) is False  # only queued records are retry-eligible
