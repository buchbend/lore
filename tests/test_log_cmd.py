"""Tests for ``lore log`` — chronological timeline of hook + run activity."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner


runner = CliRunner()

_NOW = datetime(2026, 4, 21, 12, 0, 0, tzinfo=UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _seed_vault(tmp_path: Path) -> Path:
    lore_root = tmp_path / "vault"
    (lore_root / ".lore" / "runs").mkdir(parents=True)
    (lore_root / "wiki" / "private" / "sessions").mkdir(parents=True)
    return lore_root


def _seed_hook_events(lore_root: Path, events: list[dict]) -> None:
    p = lore_root / ".lore" / "hook-events.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in events) + "\n")


def _seed_run(lore_root: Path, *, ago: timedelta, notes_new: int = 1, suffix: str = "abc123") -> None:
    run_ts = _NOW - ago
    runs_dir = lore_root / ".lore" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    stem = run_ts.strftime("%Y-%m-%dT%H-%M-%S") + f"-{suffix}"
    records = [
        {"type": "run-start", "ts": _iso(run_ts), "schema_version": 1, "run_id": stem},
        {"type": "run-end", "ts": _iso(run_ts + timedelta(seconds=15)), "schema_version": 1,
         "notes_new": notes_new, "notes_merged": 0, "duration_ms": 15000, "errors": 0},
    ]
    (runs_dir / f"{stem}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n"
    )


def _invoke(lore_root: Path, *extra: str, monkeypatch) -> str:
    from lore_cli.log_cmd import app
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    monkeypatch.setenv("_LORE_LOG_NOW", _iso(_NOW))
    result = runner.invoke(app, list(extra), catch_exceptions=False)
    return result.output


# ---------------------------------------------------------------------------
# Empty vault
# ---------------------------------------------------------------------------


def test_log_empty(tmp_path: Path, monkeypatch) -> None:
    lore_root = _seed_vault(tmp_path)
    out = _invoke(lore_root, "--since", "1h", monkeypatch=monkeypatch)
    assert "no activity" in out.lower()


# ---------------------------------------------------------------------------
# Hook events only
# ---------------------------------------------------------------------------


def test_log_hooks_only(tmp_path: Path, monkeypatch) -> None:
    lore_root = _seed_vault(tmp_path)
    _seed_hook_events(lore_root, [
        {"ts": _iso(_NOW - timedelta(minutes=10)), "event": "session-start", "outcome": "spawned-curator",
         "schema_version": 2, "pid": 12345},
        {"ts": _iso(_NOW - timedelta(minutes=5)), "event": "session-end", "outcome": "ok",
         "schema_version": 2, "pid": 12345},
    ])
    out = _invoke(lore_root, "--type", "hook", monkeypatch=monkeypatch)
    assert "session-start" in out
    assert "session-end" in out
    assert "spawned-curator" in out


# ---------------------------------------------------------------------------
# Runs only
# ---------------------------------------------------------------------------


def test_log_runs_only(tmp_path: Path, monkeypatch) -> None:
    lore_root = _seed_vault(tmp_path)
    _seed_run(lore_root, ago=timedelta(minutes=30), notes_new=3)
    out = _invoke(lore_root, "--type", "run", monkeypatch=monkeypatch)
    assert "started" in out.lower() or "curator" in out.lower()
    assert "3" in out


# ---------------------------------------------------------------------------
# Interleaved — chronological order
# ---------------------------------------------------------------------------


def test_log_interleaved(tmp_path: Path, monkeypatch) -> None:
    lore_root = _seed_vault(tmp_path)
    _seed_hook_events(lore_root, [
        {"ts": _iso(_NOW - timedelta(minutes=20)), "event": "session-start", "outcome": "spawned-curator",
         "schema_version": 2, "pid": 1},
    ])
    _seed_run(lore_root, ago=timedelta(minutes=15), notes_new=2)

    out = _invoke(lore_root, monkeypatch=monkeypatch)
    lines = [l for l in out.splitlines() if l.strip()]
    # Hook event (20m ago) should appear before run-start (15m ago)
    hook_idx = next(i for i, l in enumerate(lines) if "session-start" in l)
    # run-start line uses ">" icon and "started" outcome
    run_idx = next(i for i, l in enumerate(lines) if ">" in l and "started" in l)
    assert hook_idx < run_idx


# ---------------------------------------------------------------------------
# --since filter
# ---------------------------------------------------------------------------


def test_log_since_filter(tmp_path: Path, monkeypatch) -> None:
    lore_root = _seed_vault(tmp_path)
    _seed_hook_events(lore_root, [
        {"ts": _iso(_NOW - timedelta(hours=3)), "event": "old-event", "outcome": "ok", "schema_version": 2},
        {"ts": _iso(_NOW - timedelta(minutes=10)), "event": "recent-event", "outcome": "ok", "schema_version": 2},
    ])
    out = _invoke(lore_root, "--since", "30m", monkeypatch=monkeypatch)
    assert "recent-event" in out
    assert "old-event" not in out


# ---------------------------------------------------------------------------
# --limit
# ---------------------------------------------------------------------------


def test_log_limit(tmp_path: Path, monkeypatch) -> None:
    lore_root = _seed_vault(tmp_path)
    events = [
        {"ts": _iso(_NOW - timedelta(minutes=i)), "event": f"evt-{i}", "outcome": "ok", "schema_version": 2}
        for i in range(20)
    ]
    _seed_hook_events(lore_root, events)
    out = _invoke(lore_root, "--limit", "5", monkeypatch=monkeypatch)
    content_lines = [l for l in out.splitlines() if l.strip()]
    assert len(content_lines) == 5


# ---------------------------------------------------------------------------
# --json
# ---------------------------------------------------------------------------


def test_log_json(tmp_path: Path, monkeypatch) -> None:
    lore_root = _seed_vault(tmp_path)
    _seed_hook_events(lore_root, [
        {"ts": _iso(_NOW - timedelta(minutes=5)), "event": "session-start", "outcome": "ok", "schema_version": 2},
    ])
    out = _invoke(lore_root, "--json", monkeypatch=monkeypatch)
    for line in out.strip().splitlines():
        parsed = json.loads(line)
        assert "ts" in parsed
