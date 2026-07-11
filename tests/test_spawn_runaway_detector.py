"""Spawn pile-up detector — gates ``_spawn_detached`` on a hung prior child.

Pre-v0.37.1 incident: a Curator A child entered a 90% CPU loop (the
lockfile rmdir bug). The 60s cooldown stamp is the only check between
heartbeat-driven spawn attempts, so every minute another child joined
the pile-up, eventually 5+ stuck processes burning CPU on the same
broken state.

The runaway detector reads ``<role>.meta.json`` (written by
``_proc_wrapper``), and refuses a fresh spawn when:

* ``exit_code`` is None (prior child never exited cleanly), AND
* ``start_ts`` is older than ``runaway_age_s`` (default cooldown_s * 5), AND
* the recorded pid is alive on this host AND looks like a lore_cli process.

Tests below isolate each gate; the multi-process flock pile-up is covered
by ``test_spawn_throttle_concurrent.py`` and not duplicated here.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Helpers — write a meta.json sidecar matching `_proc_wrapper`'s shape.
# ---------------------------------------------------------------------------


def _write_meta(
    lore_root: Path,
    role: str,
    *,
    pid: int,
    start_ts: float,
    exit_code: int | None,
    end_ts: float | None = None,
) -> Path:
    proc_dir = lore_root / ".lore" / "proc"
    proc_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "pid": pid,
        "start_ts": start_ts,
        "cmd": ["python", "-m", "lore_cli", "curator", "run"],
        "exit_code": exit_code,
    }
    if end_ts is not None:
        meta["end_ts"] = end_ts
    path = proc_dir / f"{role}.meta.json"
    path.write_text(json.dumps(meta))
    return path


# ---------------------------------------------------------------------------
# _prior_spawn_runaway: gate logic
# ---------------------------------------------------------------------------


def test_runaway_returns_none_when_meta_absent(tmp_path: Path) -> None:
    from lore_cli.hooks import _prior_spawn_runaway

    assert _prior_spawn_runaway(tmp_path, "a", runaway_age_s=300) is None


def test_runaway_returns_none_when_exited(tmp_path: Path) -> None:
    from lore_cli.hooks import _prior_spawn_runaway

    _write_meta(
        tmp_path, "a",
        pid=os.getpid(), start_ts=time.time() - 9999,
        exit_code=0, end_ts=time.time() - 9000,
    )
    assert _prior_spawn_runaway(tmp_path, "a", runaway_age_s=300) is None


def test_runaway_returns_none_when_too_young(tmp_path: Path) -> None:
    """Prior child is alive but inside its normal runtime window."""
    from lore_cli.hooks import _prior_spawn_runaway

    _write_meta(
        tmp_path, "a",
        pid=os.getpid(), start_ts=time.time() - 30,
        exit_code=None,
    )
    assert _prior_spawn_runaway(tmp_path, "a", runaway_age_s=300) is None


def test_runaway_returns_none_when_pid_dead(tmp_path: Path) -> None:
    """Stale meta.json: exit_code=None but pid is gone (wrapper killed)."""
    from lore_cli.hooks import _prior_spawn_runaway

    _write_meta(
        tmp_path, "a",
        pid=999_999, start_ts=time.time() - 9999,
        exit_code=None,
    )
    # PID 999999 is unlikely to exist; if it does the cmdline check rejects it.
    with patch("lore_cli.spawn._process_is_ours", return_value=False):
        assert _prior_spawn_runaway(tmp_path, "a", runaway_age_s=300) is None


def test_runaway_detects_hung_prior_child(tmp_path: Path) -> None:
    """All conditions met → returns the runaway info dict."""
    from lore_cli.hooks import _prior_spawn_runaway

    started = time.time() - 9999
    _write_meta(
        tmp_path, "a",
        pid=os.getpid(),  # current test process — guaranteed alive
        start_ts=started,
        exit_code=None,
    )
    with patch("lore_cli.spawn._process_is_ours", return_value=True):
        info = _prior_spawn_runaway(tmp_path, "a", runaway_age_s=300)

    assert info is not None
    assert info["pid"] == os.getpid()
    assert info["age_s"] >= 9999
    assert info["start_ts"] == started


def test_runaway_returns_none_on_malformed_meta(tmp_path: Path) -> None:
    from lore_cli.hooks import _prior_spawn_runaway

    proc_dir = tmp_path / ".lore" / "proc"
    proc_dir.mkdir(parents=True, exist_ok=True)
    (proc_dir / "a.meta.json").write_text("{ not json")
    assert _prior_spawn_runaway(tmp_path, "a", runaway_age_s=300) is None


# ---------------------------------------------------------------------------
# _process_is_ours: liveness + cmdline check
# ---------------------------------------------------------------------------


def test_process_is_ours_rejects_dead_pid() -> None:
    from lore_cli.hooks import _process_is_ours

    # PID 1 is init — we'd never accept it (cmdline doesn't match lore_cli).
    # PID 999_999 is almost certainly dead; if not, cmdline rejects.
    assert _process_is_ours(999_999) is False


def test_process_is_ours_rejects_pid_zero() -> None:
    from lore_cli.hooks import _process_is_ours

    assert _process_is_ours(0) is False
    assert _process_is_ours(1) is False  # init isn't lore


# ---------------------------------------------------------------------------
# _spawn_detached: integration — runaway gate refuses Popen and logs once.
# ---------------------------------------------------------------------------


def test_spawn_detached_skips_when_prior_runaway(tmp_path: Path) -> None:
    """When _prior_spawn_runaway returns info, no subprocess is launched
    AND a single warning event is appended to hook-events.jsonl."""
    from lore_cli.hooks import _spawn_detached

    # Pre-write stamp as expired so cooldown gate would normally pass.
    stamp_dir = tmp_path / ".lore"
    stamp_dir.mkdir(parents=True, exist_ok=True)

    popen_calls: list[tuple] = []

    class FakePopen:
        def __init__(self, *args, **kwargs):
            popen_calls.append((args, kwargs))

    runaway_info = {"pid": 12345, "age_s": 9999, "start_ts": time.time() - 9999}

    with patch("subprocess.Popen", FakePopen), \
         patch("lore_cli.spawn._prior_spawn_runaway", return_value=runaway_info):
        spawned = _spawn_detached(
            tmp_path, "a",
            ["python", "-m", "lore_cli", "curator", "run"],
            cooldown_s=60,
        )

    assert spawned is False
    assert popen_calls == []  # no subprocess launched

    # Warning event was emitted to hook-events.jsonl.
    events_path = tmp_path / ".lore" / "spine.jsonl"
    assert events_path.exists(), "spawn-throttle event should be logged"
    lines = events_path.read_text().strip().splitlines()
    records = [json.loads(line) for line in lines]
    runaway_records = [
        r for r in records
        if r.get("event") == "spawn-throttle"
        and r.get("data", {}).get("outcome") == "prior-runaway"
    ]
    assert len(runaway_records) == 1
    rec = runaway_records[0]
    assert rec["data"]["role"] == "a"
    assert rec["data"]["error"]["pid"] == 12345
    assert rec["data"]["error"]["age_s"] == 9999


def test_spawn_detached_runaway_warning_throttled(tmp_path: Path) -> None:
    """Repeated runaway-blocked spawn attempts within cooldown_s * 10
    emit only one warning event (avoid hook-events.jsonl spam)."""
    from lore_cli.hooks import _spawn_detached

    runaway_info = {"pid": 12345, "age_s": 9999, "start_ts": time.time() - 9999}

    class FakePopen:
        def __init__(self, *args, **kwargs):
            raise AssertionError("Popen must not be called when runaway active")

    with patch("subprocess.Popen", FakePopen), \
         patch("lore_cli.spawn._prior_spawn_runaway", return_value=runaway_info):
        # Three back-to-back attempts within the throttle window.
        for _ in range(3):
            _spawn_detached(
                tmp_path, "a",
                ["python", "-m", "lore_cli", "curator", "run"],
                cooldown_s=60,
            )

    events_path = tmp_path / ".lore" / "spine.jsonl"
    if not events_path.exists():
        records = []
    else:
        records = [
            json.loads(line)
            for line in events_path.read_text().strip().splitlines()
            if line
        ]
    runaway_records = [
        r for r in records
        if r.get("event") == "spawn-throttle"
        and r.get("data", {}).get("outcome") == "prior-runaway"
    ]
    assert len(runaway_records) == 1, (
        f"Expected exactly one warning across 3 attempts; got {len(runaway_records)}"
    )


def test_spawn_detached_proceeds_when_no_runaway(tmp_path: Path) -> None:
    """Sanity check: with no runaway detected, spawn happens as before."""
    from lore_cli.hooks import _spawn_detached

    popen_calls: list[tuple] = []

    class FakePopen:
        def __init__(self, *args, **kwargs):
            popen_calls.append((args, kwargs))

    with patch("subprocess.Popen", FakePopen), \
         patch("lore_cli.spawn._prior_spawn_runaway", return_value=None):
        spawned = _spawn_detached(
            tmp_path, "a",
            ["python", "-m", "lore_cli", "curator", "run"],
            cooldown_s=60,
        )

    assert spawned is True
    assert len(popen_calls) == 1
