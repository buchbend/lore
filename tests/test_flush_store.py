"""Leftover flush records — read and retention only (issues #189, #377).

No code opens a record any more. What the store must still do is read the
records already on disk and let the retention janitor clear them, so these
tests seed the JSON directly rather than driving a state machine.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from lore_core.flush_store import FlushRecord, FlushState, FlushStore
from lore_core.spine import read_spine, validate_envelope


def _seed(
    lore_root: Path,
    flush_id: str,
    state: FlushState,
    *,
    age_days: float = 0,
    reason: str | None = None,
    trace_id: str | None = None,
) -> FlushRecord:
    """Write one record as a pre-#361 writer left it."""
    d = lore_root / ".lore" / "flushes"
    d.mkdir(parents=True, exist_ok=True)
    updated = (datetime.now(UTC) - timedelta(days=age_days)).isoformat().replace("+00:00", "Z")
    rec = FlushRecord(
        flush_id=flush_id,
        buffer_stem=flush_id,
        state=state.value,
        reason=reason,
        wiki="private",
        trace_id=trace_id,
        created_at=updated,
        updated_at=updated,
    )
    (d / f"{flush_id}.json").write_text(json.dumps(rec.to_dict()))
    return rec


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def test_get_returns_none_for_an_absent_record(tmp_path: Path) -> None:
    assert FlushStore(tmp_path).get("nothing") is None


def test_list_is_empty_on_a_vault_that_never_flushed(tmp_path: Path) -> None:
    assert FlushStore(tmp_path).list() == []


def test_list_filters_by_state(tmp_path: Path) -> None:
    _seed(tmp_path, "a", FlushState.PUBLISHED)
    _seed(tmp_path, "b", FlushState.DEAD_LETTERED, reason="compose-failed")
    store = FlushStore(tmp_path)

    dead = store.list(state=FlushState.DEAD_LETTERED)
    assert [r.flush_id for r in dead] == ["b"]


def test_get_round_trips_a_persisted_record(tmp_path: Path) -> None:
    _seed(tmp_path, "t1__20260101", FlushState.RUNNING, trace_id="abc123")

    rec = FlushStore(tmp_path).get("t1__20260101")
    assert rec is not None
    assert rec.state == "running"
    assert rec.trace_id == "abc123"
    assert rec.wiki == "private"


# ---------------------------------------------------------------------------
# Retention purge (#190) — the one live caller
# ---------------------------------------------------------------------------


def test_purge_deletes_old_terminal_records(tmp_path: Path) -> None:
    rec = _seed(tmp_path, "old__20260101", FlushState.PUBLISHED, age_days=40)
    store = FlushStore(tmp_path)

    result = store.purge(terminal_max_age_days=30, dead_letter_hard_cap=50)
    assert store.get(rec.flush_id) is None
    assert result.deleted == 1


def test_purge_keeps_recent_terminal_records(tmp_path: Path) -> None:
    rec = _seed(tmp_path, "recent__20260101", FlushState.WITHHELD)
    store = FlushStore(tmp_path)

    result = store.purge(terminal_max_age_days=30, dead_letter_hard_cap=50)
    assert store.get(rec.flush_id) is not None
    assert result.deleted == 0


def test_purge_leaves_unresolved_records_alone(tmp_path: Path) -> None:
    queued = _seed(tmp_path, "queued__20260101", FlushState.QUEUED, age_days=999)
    running = _seed(tmp_path, "running__20260101", FlushState.RUNNING, age_days=999)
    store = FlushStore(tmp_path)

    store.purge(terminal_max_age_days=1, dead_letter_hard_cap=1)
    assert store.get(queued.flush_id) is not None
    assert store.get(running.flush_id) is not None


def test_purge_dead_letters_survive_the_age_window(tmp_path: Path) -> None:
    rec = _seed(tmp_path, "dead__20260101", FlushState.DEAD_LETTERED, age_days=999)
    store = FlushStore(tmp_path)

    result = store.purge(terminal_max_age_days=1, dead_letter_hard_cap=50)
    assert store.get(rec.flush_id) is not None
    assert result.deleted == 0


def test_purge_caps_dead_letters_beyond_the_hard_cap(tmp_path: Path) -> None:
    dead = [
        _seed(tmp_path, f"dead{i}__20260101", FlushState.DEAD_LETTERED, age_days=i)
        for i in range(5)
    ]
    store = FlushStore(tmp_path)

    result = store.purge(terminal_max_age_days=9999, dead_letter_hard_cap=3)
    remaining = {r.flush_id for r in store.list(state=FlushState.DEAD_LETTERED)}
    assert len(remaining) == 3
    assert result.deleted == 2
    # Oldest purged first.
    assert dead[4].flush_id not in remaining
    assert dead[3].flush_id not in remaining
    assert dead[0].flush_id in remaining


def test_purge_deletion_emits_a_janitor_spine_event(tmp_path: Path) -> None:
    _seed(tmp_path, "old__20260101", FlushState.PUBLISHED, age_days=40)

    FlushStore(tmp_path).purge(terminal_max_age_days=30, dead_letter_hard_cap=50)
    events = [r for r in read_spine(tmp_path, source="janitor") if r["event"] == "retention-delete"]
    assert events
    assert events[0]["data"]["family"] == "flush-record"
    validate_envelope(events[0])


def test_purge_failure_emits_a_warn_spine_event(tmp_path: Path, monkeypatch) -> None:
    _seed(tmp_path, "old__20260101", FlushState.PUBLISHED, age_days=40)

    real_unlink = Path.unlink

    def bad_unlink(self, *args, **kwargs):
        raise PermissionError("locked")

    monkeypatch.setattr(Path, "unlink", bad_unlink)
    result = FlushStore(tmp_path).purge(terminal_max_age_days=30, dead_letter_hard_cap=50)
    monkeypatch.setattr(Path, "unlink", real_unlink)

    assert result.failed == 1
    failures = [
        r for r in read_spine(tmp_path, source="janitor") if r["event"] == "retention-delete-failed"
    ]
    assert failures
    assert failures[0]["level"] == "warn"
    assert failures[0]["data"]["family"] == "flush-record"
