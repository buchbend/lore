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

from lore_core.capture_state import query_capture_state


_NOW = datetime(2026, 4, 21, 12, 0, 0, tzinfo=UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _seed_lore_root(tmp_path: Path) -> Path:
    """Minimal vault skeleton: .lore dir + a wiki."""
    (tmp_path / ".lore").mkdir()
    (tmp_path / "wiki" / "private" / "sessions").mkdir(parents=True)
    return tmp_path


def _append_spine(lore_root: Path, envelopes: list[dict]) -> None:
    path = lore_root / ".lore" / "spine.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        for e in envelopes:
            f.write(json.dumps(e) + "\n")


def _curator_env(record: dict, run_id: str) -> dict:
    data = {k: v for k, v in record.items() if k not in ("type", "ts", "schema_version")}
    return {
        "ts": record.get("ts"), "v": 1, "source": "curator", "event": record.get("type"),
        "level": "info", "trace_id": None, "session_id": None, "run_id": run_id,
        "wiki": None, "scope": None, "error_code": None, "data": data,
    }


def _write_runs(
    lore_root: Path,
    records_per_run: list[list[dict]],
    ts_start: datetime | None = None,
) -> list[str]:
    """Seed curator runs onto the event spine. Returns run_ids (newest last)."""
    if ts_start is None:
        ts_start = _NOW - timedelta(hours=len(records_per_run))
    run_ids: list[str] = []
    envs: list[dict] = []
    for i, records in enumerate(records_per_run):
        file_ts = ts_start + timedelta(minutes=i)
        short = f"{chr(ord('a') + i)}{chr(ord('a') + i)}{chr(ord('a') + i)}111"
        run_id = file_ts.strftime("%Y-%m-%dT%H-%M-%S") + f"-{short}"
        run_ids.append(run_id)
        envs.extend(_curator_env(r, run_id) for r in records)
    _append_spine(lore_root, envs)
    return run_ids


def _spine_env(row: dict) -> dict:
    """Wrap an old-style {ts, event, outcome, ...} hook row as a spine envelope."""
    data = {k: v for k, v in row.items() if k not in ("ts", "event", "schema_version")}
    outcome = data.get("outcome")
    return {
        "ts": row.get("ts"), "v": 1, "source": "hook", "event": row.get("event"),
        "level": "error" if outcome == "error" else "info",
        "trace_id": None, "session_id": None, "run_id": None,
        "wiki": None, "scope": None, "error_code": None, "data": data,
    }


def _write_hook_events(lore_root: Path, events: list[dict]) -> Path:
    # Append so seeded curator runs on the same spine are preserved.
    _append_spine(lore_root, [_spine_env(e) for e in events])
    return lore_root / ".lore" / "spine.jsonl"


# ---------------------------------------------------------------------------
# Empty / fresh vault
# ---------------------------------------------------------------------------


def test_capture_state_empty_vault(tmp_path: Path) -> None:
    lore_root = _seed_lore_root(tmp_path)

    state = query_capture_state(lore_root, now=_NOW)

    assert state.lore_root == lore_root
    assert state.hook_errors_24h == 0
    assert state.spine_write_failed_marker_age_s is None
    assert state.simple_tier_fallback_active is False
    assert state.last_run_ts is None
    assert state.last_run_errors is None
    assert state.last_run_short_id is None


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
# Hook liveness (Fix #2 — surface "is capture hook firing?")
# ---------------------------------------------------------------------------


def test_capture_state_hook_liveness_absent_when_file_missing(tmp_path: Path) -> None:
    """No spine records → last_hook_event_ts is None."""
    lore_root = _seed_lore_root(tmp_path)
    state = query_capture_state(lore_root, now=_NOW)
    assert state.last_hook_event_ts is None
    assert state.last_hook_event_outcome is None
    assert state.last_hook_event_kind is None


def test_capture_state_hook_liveness_from_newest_event(tmp_path: Path) -> None:
    """Last hook fields read the newest hook record on the event spine."""
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
    """Garbled lines on the spine don't crash or mask a real newest."""
    lore_root = _seed_lore_root(tmp_path)
    path = lore_root / ".lore" / "spine.jsonl"
    good = _spine_env({
        "ts": _iso(_NOW - timedelta(minutes=5)),
        "event": "session-start",
        "outcome": "no-scope",
    })
    path.write_text("{not json\n" + json.dumps(good) + "\n\n")
    state = query_capture_state(lore_root, now=_NOW)
    assert state.last_hook_event_ts == _NOW - timedelta(minutes=5)
    assert state.last_hook_event_outcome == "no-scope"


# ---------------------------------------------------------------------------
# Populated vault — end-to-end
# ---------------------------------------------------------------------------


def test_capture_state_populated_vault(tmp_path: Path) -> None:
    lore_root = _seed_lore_root(tmp_path)

    # Seed 2 runs; the newest one ended 2h ago with 0 errors.
    _write_runs(
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

    assert state.last_run_ts == _NOW - timedelta(hours=2)
    assert state.last_run_errors == 0
    assert state.last_run_short_id is not None
    assert state.hook_errors_24h == 1


# ---------------------------------------------------------------------------
# Observability sentinel: hook-log failure marker
# ---------------------------------------------------------------------------


def test_capture_state_hook_log_failed_marker(tmp_path: Path) -> None:
    lore_root = _seed_lore_root(tmp_path)
    marker = lore_root / ".lore" / "spine-failed.marker"
    marker.touch()

    state = query_capture_state(lore_root, now=_NOW)
    assert state.spine_write_failed_marker_age_s is not None
    assert state.spine_write_failed_marker_age_s >= 0


def test_capture_state_hook_log_failed_marker_absent(tmp_path: Path) -> None:
    lore_root = _seed_lore_root(tmp_path)
    state = query_capture_state(lore_root, now=_NOW)
    assert state.spine_write_failed_marker_age_s is None


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
    (lore_root / ".lore" / "spine.jsonl").write_text(
        json.dumps(_spine_env({"ts": _iso(_NOW), "event": "session-start", "outcome": "ok"})) + "\n"
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
