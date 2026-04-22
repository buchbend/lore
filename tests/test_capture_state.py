"""Task 10: CaptureState — single source of liveness truth.

After Task 10, doctor's capture panel, the SessionStart banner, the
lore status command, and /lore:loaded's live section all render from
the same CaptureState snapshot. Before it, three renderers each reach
into files directly with subtly different logic.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lore_core.capture_state import (
    CaptureState,
    CuratorStatus,
    query_capture_state,
)


_NOW = datetime(2026, 4, 21, 12, 0, 0, tzinfo=UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _seed_lore_root(tmp_path: Path) -> Path:
    """Minimal vault skeleton: .lore dir + a wiki."""
    (tmp_path / ".lore").mkdir()
    (tmp_path / "wiki" / "private" / "sessions").mkdir(parents=True)
    return tmp_path


def _write_runs(
    lore_root: Path,
    records_per_run: list[list[dict]],
    ts_start: datetime | None = None,
) -> list[Path]:
    """Write synthetic archival run files. Returns paths (newest last)."""
    runs_dir = lore_root / ".lore" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    if ts_start is None:
        ts_start = _NOW - timedelta(hours=len(records_per_run))
    paths: list[Path] = []
    for i, records in enumerate(records_per_run):
        file_ts = ts_start + timedelta(minutes=i)
        short = f"{chr(ord('a') + i)}{chr(ord('a') + i)}{chr(ord('a') + i)}111"
        stem = file_ts.strftime("%Y-%m-%dT%H-%M-%S") + f"-{short}"
        path = runs_dir / f"{stem}.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        paths.append(path)
    return paths


def _write_hook_events(lore_root: Path, events: list[dict]) -> Path:
    path = lore_root / ".lore" / "hook-events.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return path


# ---------------------------------------------------------------------------
# Empty / fresh vault
# ---------------------------------------------------------------------------


def test_capture_state_empty_vault(tmp_path: Path) -> None:
    lore_root = _seed_lore_root(tmp_path)

    state = query_capture_state(lore_root, now=_NOW)

    assert state.lore_root == lore_root
    assert state.pending_transcripts == 0
    assert state.hook_errors_24h == 0
    assert state.last_note_filed is None
    assert state.hook_log_failed_marker_age_s is None
    assert state.simple_tier_fallback_active is False
    assert len(state.curators) == 3
    assert [c.role for c in state.curators] == ["a", "b", "c"]
    for c in state.curators:
        assert c.last_run_ts is None
        assert c.last_run_notes_new is None
        assert c.last_run_errors is None
        assert c.last_run_short_id is None
        assert c.work_lock_held is False
        assert c.overdue is True  # never-run = overdue by definition


# ---------------------------------------------------------------------------
# Scope resolution
# ---------------------------------------------------------------------------


def test_capture_state_unattached_cwd(tmp_path: Path) -> None:
    lore_root = _seed_lore_root(tmp_path)
    unrelated = tmp_path / "elsewhere"
    unrelated.mkdir()

    state = query_capture_state(lore_root, cwd=unrelated, now=_NOW)
    assert state.scope_attached is False
    assert state.scope_name is None
    assert state.scope_root is None


def test_capture_state_scope_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from lore_core.state.attachments import Attachment, AttachmentsFile

    lore_root = _seed_lore_root(tmp_path)
    project = tmp_path / "proj"
    project.mkdir()

    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    af = AttachmentsFile(lore_root); af.load()
    af.add(Attachment(
        path=project, wiki="private", scope="proj:test",
        attached_at=_NOW, source="manual",
    ))
    af.save()

    state = query_capture_state(lore_root, cwd=project, now=_NOW)
    assert state.scope_attached is True
    assert state.scope_name == "private/proj:test"
    assert state.scope_root == project


# ---------------------------------------------------------------------------
# Per-role curator status + overdue calculation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "role,last_run_ago,expected_overdue",
    [
        ("a", timedelta(hours=1), False),
        ("a", timedelta(hours=25), True),
        ("b", timedelta(hours=6), False),  # same calendar day as _NOW
        ("b", timedelta(days=1, hours=2), True),  # prior calendar day
        ("c", timedelta(days=6), False),
        ("c", timedelta(days=8), True),
    ],
)
def test_capture_state_overdue_calculation_per_role(
    tmp_path: Path, role: str, last_run_ago: timedelta, expected_overdue: bool
) -> None:
    from lore_core.ledger import WikiLedger

    lore_root = _seed_lore_root(tmp_path)
    wledger = WikiLedger(lore_root, "private")
    wledger.update_last_curator(role, at=_NOW - last_run_ago)

    state = query_capture_state(lore_root, now=_NOW)
    statuses = {c.role: c for c in state.curators}
    assert statuses[role].overdue is expected_overdue, (
        f"role={role} ago={last_run_ago} expected overdue={expected_overdue}"
    )


# ---------------------------------------------------------------------------
# Hook liveness (Fix #2 — surface "is capture hook firing?")
# ---------------------------------------------------------------------------


def test_capture_state_hook_liveness_absent_when_file_missing(tmp_path: Path) -> None:
    """No hook-events.jsonl → last_hook_event_ts is None."""
    lore_root = _seed_lore_root(tmp_path)
    state = query_capture_state(lore_root, now=_NOW)
    assert state.last_hook_event_ts is None
    assert state.last_hook_event_outcome is None
    assert state.last_hook_event_kind is None


def test_capture_state_hook_liveness_from_newest_event(tmp_path: Path) -> None:
    """Last hook fields read the newest record in hook-events.jsonl."""
    lore_root = _seed_lore_root(tmp_path)
    # Mix of ages; newest should win regardless of file order.
    _write_hook_events(
        lore_root,
        [
            {
                "ts": _iso(_NOW - timedelta(hours=6)),
                "event": "session-end",
                "outcome": "below-threshold",
            },
            {
                "ts": _iso(_NOW - timedelta(minutes=10)),
                "event": "session-start",
                "outcome": "spawned-curator",
            },
            {
                "ts": _iso(_NOW - timedelta(hours=3)),
                "event": "pre-compact",
                "outcome": "no-new-turns",
            },
        ],
    )
    state = query_capture_state(lore_root, now=_NOW)
    assert state.last_hook_event_ts == _NOW - timedelta(minutes=10)
    assert state.last_hook_event_outcome == "spawned-curator"
    assert state.last_hook_event_kind == "session-start"


def test_capture_state_hook_liveness_skips_malformed_lines(tmp_path: Path) -> None:
    """Garbled lines in hook-events.jsonl don't crash or mask a real newest."""
    lore_root = _seed_lore_root(tmp_path)
    path = lore_root / ".lore" / "hook-events.jsonl"
    good = {
        "ts": _iso(_NOW - timedelta(minutes=5)),
        "event": "session-start",
        "outcome": "no-scope",
    }
    path.write_text("{not json\n" + json.dumps(good) + "\n\n")
    state = query_capture_state(lore_root, now=_NOW)
    assert state.last_hook_event_ts == _NOW - timedelta(minutes=5)
    assert state.last_hook_event_outcome == "no-scope"


# ---------------------------------------------------------------------------
# Populated vault — end-to-end
# ---------------------------------------------------------------------------


def test_capture_state_populated_vault(tmp_path: Path) -> None:
    from lore_core.ledger import WikiLedger

    lore_root = _seed_lore_root(tmp_path)

    # Seed last_curator_{a,b,c} at different ages.
    wledger = WikiLedger(lore_root, "private")
    wledger.update_last_curator("a", at=_NOW - timedelta(hours=2))
    wledger.update_last_curator("b", at=_NOW - timedelta(hours=3))
    wledger.update_last_curator("c", at=_NOW - timedelta(days=6))

    # Seed 3 runs, last one recent with 2 new notes and 0 errors.
    paths = _write_runs(
        lore_root,
        [
            [
                {"type": "run-start", "ts": _iso(_NOW - timedelta(hours=3)), "schema_version": 1},
                {"type": "run-end", "ts": _iso(_NOW - timedelta(hours=3)), "notes_new": 1, "notes_merged": 0, "errors": 0},
            ],
            [
                {"type": "run-start", "ts": _iso(_NOW - timedelta(hours=2)), "schema_version": 1},
                {
                    "type": "session-note",
                    "ts": _iso(_NOW - timedelta(hours=2)),
                    "action": "filed",
                    "wikilink": "[[2026-04-21-test-note]]",
                },
                {"type": "run-end", "ts": _iso(_NOW - timedelta(hours=2)), "notes_new": 2, "notes_merged": 0, "errors": 0},
            ],
        ],
        ts_start=_NOW - timedelta(hours=3),
    )

    # Seed 5 hook events, 1 error within 24h.
    _write_hook_events(
        lore_root,
        [
            {"ts": _iso(_NOW - timedelta(hours=i)), "event": "session-start", "outcome": "below-threshold"}
            for i in [1, 2, 3]
        ] + [
            {"ts": _iso(_NOW - timedelta(hours=1)), "event": "session-end", "outcome": "error", "error": {"type": "Boom"}},
            {"ts": _iso(_NOW - timedelta(hours=30)), "event": "session-end", "outcome": "error", "error": {"type": "Boom"}},  # >24h, excluded
        ],
    )

    state = query_capture_state(lore_root, now=_NOW)

    a, b, c = state.curators
    assert a.last_run_ts == _NOW - timedelta(hours=2)
    assert a.last_run_notes_new == 2
    assert a.last_run_errors == 0
    assert a.last_run_short_id is not None
    assert a.overdue is False

    assert b.last_run_ts == _NOW - timedelta(hours=3)
    assert c.last_run_ts == _NOW - timedelta(days=6)

    assert state.last_note_filed is not None
    note_ts, wikilink = state.last_note_filed
    assert wikilink == "[[2026-04-21-test-note]]"

    assert state.hook_errors_24h == 1


# ---------------------------------------------------------------------------
# Pending transcripts
# ---------------------------------------------------------------------------


def test_capture_state_pending_transcripts_count(tmp_path: Path) -> None:
    from lore_core.ledger import TranscriptLedger, TranscriptLedgerEntry

    lore_root = _seed_lore_root(tmp_path)
    tledger = TranscriptLedger(lore_root)
    for i in range(3):
        tledger.upsert(
            TranscriptLedgerEntry(
                host="fake",
                transcript_id=f"t{i}",
                path=lore_root / f"t{i}.jsonl",
                directory=lore_root,
                digested_hash=None,
                digested_index_hint=None,
                synthesised_hash=None,
                last_mtime=_NOW,
                curator_a_run=None,
                noteworthy=None,
                session_note=None,
            )
        )

    state = query_capture_state(lore_root, now=_NOW)
    assert state.pending_transcripts == 3


# ---------------------------------------------------------------------------
# Work-lock detection
# ---------------------------------------------------------------------------


def test_capture_state_work_lock_held(tmp_path: Path) -> None:
    lore_root = _seed_lore_root(tmp_path)
    lock_dir = lore_root / ".lore" / "curator.lock"
    lock_dir.mkdir()

    state = query_capture_state(lore_root, now=_NOW)
    assert any(c.work_lock_held for c in state.curators), (
        "at least one curator should report work_lock_held=True"
    )


# ---------------------------------------------------------------------------
# Observability sentinel: hook-log failure marker
# ---------------------------------------------------------------------------


def test_capture_state_hook_log_failed_marker(tmp_path: Path) -> None:
    lore_root = _seed_lore_root(tmp_path)
    marker = lore_root / ".lore" / "hook-log-failed.marker"
    marker.touch()

    state = query_capture_state(lore_root, now=_NOW)
    assert state.hook_log_failed_marker_age_s is not None
    assert state.hook_log_failed_marker_age_s >= 0


def test_capture_state_hook_log_failed_marker_absent(tmp_path: Path) -> None:
    lore_root = _seed_lore_root(tmp_path)
    state = query_capture_state(lore_root, now=_NOW)
    assert state.hook_log_failed_marker_age_s is None


# ---------------------------------------------------------------------------
# Simple-tier fallback sentinel
# ---------------------------------------------------------------------------


def test_capture_state_simple_tier_fallback_active(tmp_path: Path) -> None:
    lore_root = _seed_lore_root(tmp_path)
    (lore_root / ".lore" / "warnings.log").write_text(
        "2026-04-21T10:00:00Z simple-tier-fallback\n"
    )
    state = query_capture_state(lore_root, now=_NOW)
    assert state.simple_tier_fallback_active is True


def test_capture_state_simple_tier_fallback_inactive(tmp_path: Path) -> None:
    lore_root = _seed_lore_root(tmp_path)
    state = query_capture_state(lore_root, now=_NOW)
    assert state.simple_tier_fallback_active is False


# ---------------------------------------------------------------------------
# Read-only contract + perf guard
# ---------------------------------------------------------------------------


def test_capture_state_query_is_readonly(tmp_path: Path) -> None:
    """Snapshot .lore/ mtimes before and after query; they must be identical."""
    lore_root = _seed_lore_root(tmp_path)
    # Populate with one of everything.
    (lore_root / ".lore" / "hook-events.jsonl").write_text(
        json.dumps({"ts": _iso(_NOW), "event": "session-start", "outcome": "ok"}) + "\n"
    )
    _write_runs(
        lore_root,
        [[
            {"type": "run-start", "ts": _iso(_NOW), "schema_version": 1},
            {"type": "run-end", "ts": _iso(_NOW), "notes_new": 0, "errors": 0},
        ]],
    )

    def snapshot() -> dict[str, int]:
        out = {}
        for p in (lore_root / ".lore").rglob("*"):
            if p.is_file():
                out[str(p)] = p.stat().st_mtime_ns
        return out

    before = snapshot()
    query_capture_state(lore_root, now=_NOW)
    after = snapshot()
    assert before == after


def test_capture_state_query_is_fast(tmp_path: Path) -> None:
    """<100ms on a vault with 200 runs and ~1000 hook events."""
    lore_root = _seed_lore_root(tmp_path)

    # 200 runs
    _write_runs(
        lore_root,
        [
            [
                {"type": "run-start", "ts": _iso(_NOW - timedelta(minutes=i)), "schema_version": 1},
                {"type": "run-end", "ts": _iso(_NOW - timedelta(minutes=i)), "notes_new": 0, "errors": 0},
            ]
            for i in range(200)
        ],
    )
    # ~1000 hook events
    _write_hook_events(
        lore_root,
        [
            {"ts": _iso(_NOW - timedelta(minutes=i)), "event": "session-start", "outcome": "ok"}
            for i in range(1000)
        ],
    )

    start = time.monotonic()
    query_capture_state(lore_root, now=_NOW)
    elapsed = time.monotonic() - start
    assert elapsed < 0.2, f"query_capture_state took {elapsed*1000:.1f}ms; expected <200ms"
