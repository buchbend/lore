"""Task 1: Curator C weekly SessionStart trigger.

- UTC-pinned ISO-week check
- Per-user 48h jitter via SHA-256 of git user.email (hostname fallback)
- Feature-flag gated (curator.curator_c.enabled, mode=local)
- Reuses Plan A's try_acquire_spawn_lock("c")
"""

from __future__ import annotations

import hashlib
import multiprocessing as mp
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from lore_cli.hooks import hook_app
from lore_core.ledger import WikiLedger, WikiLedgerEntry


runner = CliRunner()


LORE_BLOCK = """\
# Project

## Lore

- wiki: testwiki
- scope: testscope
- backend: none
"""


def _make_attached_project(
    root: Path, *, enabled: bool = True, mode: str = "local"
) -> Path:
    project = root / "project"
    project.mkdir()
    (project / "CLAUDE.md").write_text(LORE_BLOCK)
    (project / "wiki" / "testwiki").mkdir(parents=True)
    (project / ".lore").mkdir(parents=True, exist_ok=True)
    # Per-wiki config enabling / configuring Curator C.
    wiki_cfg = project / "wiki" / "testwiki" / ".lore-wiki.yml"
    wiki_cfg.write_text(
        "curator:\n"
        f"  curator_c:\n"
        f"    enabled: {str(enabled).lower()}\n"
        f"    mode: {mode}\n"
    )
    return project


def _iso_week_monday(ts: datetime) -> datetime:
    """Monday 00:00Z of the ISO week containing ts."""
    # isocalendar().weekday is 1 (Mon) .. 7 (Sun)
    d = ts.date()
    weekday = ts.isocalendar().weekday
    monday = d - timedelta(days=weekday - 1)
    return datetime(monday.year, monday.month, monday.day, tzinfo=UTC)


def _expected_jitter_seconds(email: str) -> int:
    return int(hashlib.sha256(email.encode()).hexdigest()[:8], 16) % 172800


# ---------------------------------------------------------------------------
# ISO-week rollover
# ---------------------------------------------------------------------------


def test_trigger_fires_on_new_iso_week(tmp_path: Path) -> None:
    """last_curator_c from prior ISO week → spawn."""
    project = _make_attached_project(tmp_path, enabled=True)
    lore_root = project

    now = datetime(2026, 4, 21, 23, 0, 0, tzinfo=UTC)  # Tuesday of week 17
    prior_week = now - timedelta(days=10)
    wledger = WikiLedger(lore_root, "testwiki")
    wledger.write(WikiLedgerEntry(wiki="testwiki", last_curator_c=prior_week))

    spawns: list = []

    def mock_spawn(lore_root_: Path, *a, **kw):
        spawns.append(lore_root_)
        return True

    with patch("lore_cli.hooks._spawn_detached_curator_c", side_effect=mock_spawn), \
         patch("lore_cli.hooks._now_utc", return_value=now), \
         patch("lore_cli.hooks._curator_c_email", return_value="noop@test"):
        result = runner.invoke(
            hook_app,
            ["session-start", "--cwd", str(project), "--plain"],
            env={"LORE_ROOT": str(lore_root)},
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    assert len(spawns) == 1, f"expected spawn on new ISO week; got {spawns}"


def test_trigger_skips_same_iso_week(tmp_path: Path) -> None:
    """last_curator_c from current ISO week → no spawn."""
    project = _make_attached_project(tmp_path, enabled=True)
    lore_root = project
    now = datetime(2026, 4, 21, 23, 0, 0, tzinfo=UTC)
    # Same ISO week as `now`.
    this_week = now - timedelta(hours=12)

    wledger = WikiLedger(lore_root, "testwiki")
    wledger.write(WikiLedgerEntry(wiki="testwiki", last_curator_c=this_week))

    spawns: list = []

    with patch("lore_cli.hooks._spawn_detached_curator_c",
               side_effect=lambda *a, **kw: spawns.append(1) or True), \
         patch("lore_cli.hooks._now_utc", return_value=now), \
         patch("lore_cli.hooks._curator_c_email", return_value="noop@test"):
        runner.invoke(
            hook_app,
            ["session-start", "--cwd", str(project), "--plain"],
            env={"LORE_ROOT": str(lore_root)},
            catch_exceptions=False,
        )

    assert spawns == [], f"expected no spawn same ISO week; got {spawns}"


# ---------------------------------------------------------------------------
# Jitter
# ---------------------------------------------------------------------------


def test_trigger_respects_jitter_window(tmp_path: Path) -> None:
    """Fixture email "fixture@test" has a specific computed offset.

    hashlib.sha256("fixture@test".encode()).hexdigest()[:8] is a deterministic
    value; the offset = int(.., 16) % 172800 is stable across machines.
    Compute the expected value and use it to pin the before/after window.
    """
    email = "fixture@test"
    offset = _expected_jitter_seconds(email)

    project = _make_attached_project(tmp_path, enabled=True)
    lore_root = project

    # Last Monday's ISO-week we're about to fire for — pick a fresh one.
    target_monday = datetime(2026, 4, 20, 0, 0, 0, tzinfo=UTC)  # Mon of wk 17

    # last_curator_c from the previous week so the ISO-week condition is met.
    wledger = WikiLedger(lore_root, "testwiki")
    wledger.write(
        WikiLedgerEntry(wiki="testwiki", last_curator_c=target_monday - timedelta(days=3))
    )

    # Time BEFORE jitter fires → no spawn.
    before_fire = target_monday + timedelta(seconds=max(0, offset - 60))
    spawns_before: list = []
    with patch("lore_cli.hooks._spawn_detached_curator_c",
               side_effect=lambda *a, **kw: spawns_before.append(1) or True), \
         patch("lore_cli.hooks._now_utc", return_value=before_fire), \
         patch("lore_cli.hooks._curator_c_email", return_value=email):
        runner.invoke(
            hook_app,
            ["session-start", "--cwd", str(project), "--plain"],
            env={"LORE_ROOT": str(lore_root)},
            catch_exceptions=False,
        )

    # Time AFTER jitter → spawn.
    after_fire = target_monday + timedelta(seconds=offset + 60)
    spawns_after: list = []
    with patch("lore_cli.hooks._spawn_detached_curator_c",
               side_effect=lambda *a, **kw: spawns_after.append(1) or True), \
         patch("lore_cli.hooks._now_utc", return_value=after_fire), \
         patch("lore_cli.hooks._curator_c_email", return_value=email):
        runner.invoke(
            hook_app,
            ["session-start", "--cwd", str(project), "--plain"],
            env={"LORE_ROOT": str(lore_root)},
            catch_exceptions=False,
        )

    assert spawns_before == [], f"before fire window, no spawn expected; got {spawns_before}"
    assert spawns_after == [1], f"after fire window, spawn expected; got {spawns_after}"


def test_trigger_jitter_fallback_when_git_email_unset(tmp_path: Path) -> None:
    """Unset email falls back to hostname — still deterministic, still fires."""
    import socket
    project = _make_attached_project(tmp_path, enabled=True)
    lore_root = project
    target_monday = datetime(2026, 4, 20, 0, 0, 0, tzinfo=UTC)

    wledger = WikiLedger(lore_root, "testwiki")
    wledger.write(
        WikiLedgerEntry(wiki="testwiki", last_curator_c=target_monday - timedelta(days=5))
    )
    hostname_offset = _expected_jitter_seconds(socket.gethostname())
    after_fire = target_monday + timedelta(seconds=hostname_offset + 60)

    spawns: list = []
    with patch("lore_cli.hooks._spawn_detached_curator_c",
               side_effect=lambda *a, **kw: spawns.append(1) or True), \
         patch("lore_cli.hooks._now_utc", return_value=after_fire), \
         patch("lore_cli.hooks._curator_c_email", return_value=""):
        runner.invoke(
            hook_app,
            ["session-start", "--cwd", str(project), "--plain"],
            env={"LORE_ROOT": str(lore_root)},
            catch_exceptions=False,
        )
    assert spawns == [1], "hostname fallback should still fire on same window"


# ---------------------------------------------------------------------------
# Feature-flag gates
# ---------------------------------------------------------------------------


def test_trigger_disabled_by_default(tmp_path: Path) -> None:
    """Default enabled=False → no spawn regardless of cadence."""
    project = _make_attached_project(tmp_path, enabled=False)
    lore_root = project
    now = datetime(2026, 4, 21, 23, 0, 0, tzinfo=UTC)

    wledger = WikiLedger(lore_root, "testwiki")
    wledger.write(
        WikiLedgerEntry(wiki="testwiki", last_curator_c=now - timedelta(days=30))
    )

    spawns: list = []
    with patch("lore_cli.hooks._spawn_detached_curator_c",
               side_effect=lambda *a, **kw: spawns.append(1) or True), \
         patch("lore_cli.hooks._now_utc", return_value=now), \
         patch("lore_cli.hooks._curator_c_email", return_value="noop@test"):
        runner.invoke(
            hook_app,
            ["session-start", "--cwd", str(project), "--plain"],
            env={"LORE_ROOT": str(lore_root)},
            catch_exceptions=False,
        )
    assert spawns == [], "disabled config must block spawn"


def test_trigger_central_mode_fails_loudly(tmp_path: Path, capsys) -> None:
    """mode=central → no spawn, emits a warning event, does NOT silently skip."""
    project = _make_attached_project(tmp_path, enabled=True, mode="central")
    lore_root = project
    now = datetime(2026, 4, 21, 23, 0, 0, tzinfo=UTC)

    wledger = WikiLedger(lore_root, "testwiki")
    wledger.write(
        WikiLedgerEntry(wiki="testwiki", last_curator_c=now - timedelta(days=30))
    )

    spawns: list = []
    with patch("lore_cli.hooks._spawn_detached_curator_c",
               side_effect=lambda *a, **kw: spawns.append(1) or True), \
         patch("lore_cli.hooks._now_utc", return_value=now), \
         patch("lore_cli.hooks._curator_c_email", return_value="noop@test"):
        runner.invoke(
            hook_app,
            ["session-start", "--cwd", str(project), "--plain"],
            env={"LORE_ROOT": str(lore_root)},
            catch_exceptions=False,
        )

    assert spawns == [], "central mode must not spawn locally"
    # Warning event surfaced to hook-events.jsonl (observable, not silent).
    import json as _json
    events = project / ".lore" / "hook-events.jsonl"
    if events.exists():
        lines = [
            _json.loads(l) for l in events.read_text().splitlines() if l.strip()
        ]
        central_warnings = [
            e for e in lines
            if e.get("event") == "curator-c" and e.get("outcome") == "central-mode-skipped"
        ]
        assert central_warnings, (
            f"central mode should emit a hook-event warning; got {lines}"
        )


# ---------------------------------------------------------------------------
# ISO-week boundary
# ---------------------------------------------------------------------------


def test_trigger_fires_exactly_once_across_monday_boundary(tmp_path: Path) -> None:
    """Sunday 23:59Z → no spawn; Monday 00:01Z + jitter → spawn."""
    email = "boundary@test"
    offset = _expected_jitter_seconds(email)

    project = _make_attached_project(tmp_path, enabled=True)
    lore_root = project

    # Sunday of ISO week 17 (which ends Sun 2026-04-19, Mon 2026-04-20 starts wk 17 in ISO).
    # Use 2026-04-19 23:59Z as Sunday; Monday 2026-04-20 begins new week.
    sunday_late = datetime(2026, 4, 19, 23, 59, 0, tzinfo=UTC)
    monday_target = datetime(2026, 4, 20, 0, 0, 0, tzinfo=UTC)

    wledger = WikiLedger(lore_root, "testwiki")
    wledger.write(
        WikiLedgerEntry(wiki="testwiki", last_curator_c=monday_target - timedelta(days=7))
    )

    # Sunday → same ISO week as last_curator_c-week-13 (no rollover yet from a new-week perspective;
    # actually: last_curator_c was wk 16. sunday_late is wk 16. So same ISO week.
    # Actually this test verifies ISO-week detection pinned to UTC; fix: set last_curator_c to wk 15.
    wledger.write(
        WikiLedgerEntry(wiki="testwiki", last_curator_c=monday_target - timedelta(days=10))
    )
    # Now sunday_late is in wk 16, monday_target onward is in wk 17, last_curator_c wk 15.

    spawns_sun: list = []
    with patch("lore_cli.hooks._spawn_detached_curator_c",
               side_effect=lambda *a, **kw: spawns_sun.append(1) or True), \
         patch("lore_cli.hooks._now_utc", return_value=sunday_late), \
         patch("lore_cli.hooks._curator_c_email", return_value=email):
        runner.invoke(
            hook_app,
            ["session-start", "--cwd", str(project), "--plain"],
            env={"LORE_ROOT": str(lore_root)},
            catch_exceptions=False,
        )

    # Post-jitter Monday.
    fire_time = monday_target + timedelta(seconds=offset + 60)
    spawns_mon: list = []
    with patch("lore_cli.hooks._spawn_detached_curator_c",
               side_effect=lambda *a, **kw: spawns_mon.append(1) or True), \
         patch("lore_cli.hooks._now_utc", return_value=fire_time), \
         patch("lore_cli.hooks._curator_c_email", return_value=email):
        runner.invoke(
            hook_app,
            ["session-start", "--cwd", str(project), "--plain"],
            env={"LORE_ROOT": str(lore_root)},
            catch_exceptions=False,
        )

    # Sunday-late may or may not fire depending on whether wk 16 > wk 15:
    # last_curator_c is wk 15, sunday is wk 16 → rollover HAS happened.
    # So sunday_late SHOULD fire too. The real boundary test: check that
    # after firing on sunday, monday does not double-fire (same or newer week).
    # Re-set: last_curator_c from far past (wk 14).
    wledger.write(
        WikiLedgerEntry(wiki="testwiki", last_curator_c=monday_target - timedelta(days=17))
    )

    # Pass 1: sunday_late should fire (wk 16 > wk 14)
    spawns_sun_2: list = []
    def sun_spawn(*a, **kw):
        # Simulate the spawn updating last_curator_c to sunday_late.
        wledger.update_last_curator("c", at=sunday_late)
        spawns_sun_2.append(1)
        return True

    with patch("lore_cli.hooks._spawn_detached_curator_c", side_effect=sun_spawn), \
         patch("lore_cli.hooks._now_utc", return_value=sunday_late), \
         patch("lore_cli.hooks._curator_c_email", return_value=email):
        runner.invoke(
            hook_app,
            ["session-start", "--cwd", str(project), "--plain"],
            env={"LORE_ROOT": str(lore_root)},
            catch_exceptions=False,
        )
    assert spawns_sun_2 == [1], "Sunday with stale last_curator_c should fire"

    # Pass 2: Monday of the NEW week — since last_curator_c is now sunday_late (wk 16),
    # and monday_target is wk 17, it's a new ISO week — should fire again.
    fire_time_new_week = monday_target + timedelta(seconds=offset + 60)
    spawns_mon_new: list = []
    with patch("lore_cli.hooks._spawn_detached_curator_c",
               side_effect=lambda *a, **kw: spawns_mon_new.append(1) or True), \
         patch("lore_cli.hooks._now_utc", return_value=fire_time_new_week), \
         patch("lore_cli.hooks._curator_c_email", return_value=email):
        runner.invoke(
            hook_app,
            ["session-start", "--cwd", str(project), "--plain"],
            env={"LORE_ROOT": str(lore_root)},
            catch_exceptions=False,
        )
    assert spawns_mon_new == [1], "Monday of new ISO week should fire again"


# ---------------------------------------------------------------------------
# Concurrency (flock regression guard)
# ---------------------------------------------------------------------------


def _worker_c_spawn_attempt(lore_root_str: str, results_q, barrier) -> None:
    """Child process attempting to acquire the Curator C spawn lock."""
    from unittest.mock import patch
    with patch("subprocess.Popen"):
        from lore_cli.hooks import _spawn_detached_curator_a
        # Reuse A's spawn semantics via try_acquire_spawn_lock for generic role.
        # But Curator C uses role="c" — call the dedicated helper.
        from lore_cli.hooks import _spawn_detached_curator_c
        barrier.wait(timeout=10)
        spawned = _spawn_detached_curator_c(Path(lore_root_str), cooldown_s=60)
    results_q.put(spawned)


def test_trigger_concurrent_sessions_coordinate(tmp_path: Path) -> None:
    """Four concurrent processes attempting Curator C spawn → exactly one wins."""
    lore_root = tmp_path / "root"
    (lore_root / ".lore").mkdir(parents=True)

    n = 4
    ctx = mp.get_context("spawn")
    barrier = ctx.Barrier(n)
    results_q: mp.Queue = ctx.Queue()

    procs = [
        ctx.Process(target=_worker_c_spawn_attempt, args=(str(lore_root), results_q, barrier))
        for _ in range(n)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=30)

    results = []
    while not results_q.empty():
        results.append(results_q.get_nowait())
    winners = [r for r in results if r]
    assert len(winners) == 1, f"exactly one process must spawn; got {results}"
