"""Task 2: flock-based spawn throttle; closes #17.

The old spawn throttle was:
    read stamp → check cooldown → Popen() → write stamp

Two concurrent SessionEnd hooks both read the stale stamp, both passed
the cooldown gate, both Popen'd. No mutual exclusion; the stamp was
written AFTER the spawn decision.

New throttle uses fcntl.flock(LOCK_EX | LOCK_NB) on a per-role
.spawn.lock file. Two primitives cleanly separated:
  * Mutual exclusion: flock — auto-releases on process exit.
  * Cooldown: timestamp file read AT lock-acquire, written AFTER a
    successful spawn.

Multi-process tests (NOT threads — threads share FDs and can mask
flock races). Uses multiprocessing.Barrier so all processes cross
the gate within microseconds.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Worker functions — must be top-level for pickling.
# ---------------------------------------------------------------------------


def _worker_spawn_attempt(lore_root_str: str, barrier_id: int, results_q, barrier) -> None:
    """Child process: patch Popen, cross barrier, attempt spawn.

    Returns via results_q: (barrier_id, spawned: bool).
    """
    from unittest.mock import patch

    # Patch Popen so the test doesn't actually spawn lore subprocesses.
    popen_call_count = [0]

    class FakePopen:
        def __init__(self, *args, **kwargs):
            popen_call_count[0] += 1

    with patch("subprocess.Popen", FakePopen):
        from lore_cli.hooks import _spawn_detached_curator_a

        barrier.wait(timeout=10)  # sync all children to the gate
        spawned = _spawn_detached_curator_a(Path(lore_root_str), cooldown_s=60)

    results_q.put((barrier_id, spawned, popen_call_count[0]))


def _worker_acquire_and_crash(lore_root_str: str, ready_file_str: str) -> None:
    """Child process: acquire spawn lock, write ready-file, crash without releasing.

    Tests that flock auto-releases on process exit (the whole reason we
    chose flock over O_EXCL). A file is used instead of a Queue because
    os._exit skips buffer flushing that multiprocessing.Queue relies on.
    """
    from lore_core.lockfile import try_acquire_spawn_lock

    cm = try_acquire_spawn_lock(Path(lore_root_str), "a")
    held, _stamp = cm.__enter__()
    # Synchronous file write (fsync for paranoia) so the parent sees the signal
    # even though os._exit skips Python cleanup.
    fd = os.open(ready_file_str, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, b"1" if held else b"0")
        os.fsync(fd)
    finally:
        os.close(fd)
    # Deliberately do NOT call cm.__exit__ — simulate a crash.
    os._exit(1)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _make_lore_root(tmp_path: Path) -> Path:
    root = tmp_path / "lore_root"
    (root / ".lore").mkdir(parents=True)
    (root / "wiki" / "testwiki").mkdir(parents=True)
    return root


def test_concurrent_spawns_only_one_wins_multiprocess(tmp_path: Path) -> None:
    """8 concurrent processes attempting spawn → exactly one wins.

    This is the #17 regression guard. Uses multiprocessing (not threads)
    with a Barrier so all processes cross the gate within microseconds.
    """
    lore_root = _make_lore_root(tmp_path)

    n = 8
    ctx = mp.get_context("spawn")
    barrier = ctx.Barrier(n)
    results_q: mp.Queue = ctx.Queue()

    procs = [
        ctx.Process(target=_worker_spawn_attempt, args=(str(lore_root), i, results_q, barrier))
        for i in range(n)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=30)
        assert not p.is_alive(), f"worker {p.pid} hung"

    results = []
    while not results_q.empty():
        results.append(results_q.get_nowait())
    assert len(results) == n, f"expected {n} results, got {len(results)}: {results}"

    winners = [r for r in results if r[1]]
    assert len(winners) == 1, (
        f"exactly one process must win the spawn lock; got {len(winners)}: "
        f"{results}"
    )
    # The winner actually called Popen; losers did not.
    winner = winners[0]
    assert winner[2] == 1, f"winner should have called Popen once, got {winner[2]}"
    losers_popen = sum(r[2] for r in results if not r[1])
    assert losers_popen == 0, f"losers must not call Popen, got {losers_popen}"


def test_stale_lock_reclaimed_after_spawner_crash(tmp_path: Path) -> None:
    """flock auto-releases on process exit — no stale-lock orphan problem.

    Child acquires the spawn lock then os._exit(1) without releasing.
    Parent immediately acquires the same lock successfully.
    """
    lore_root = _make_lore_root(tmp_path)
    ready_file = tmp_path / "ready"

    ctx = mp.get_context("spawn")
    p = ctx.Process(
        target=_worker_acquire_and_crash, args=(str(lore_root), str(ready_file))
    )
    p.start()

    # Wait for the ready-file to appear.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if ready_file.exists():
            break
        time.sleep(0.05)
    assert ready_file.exists(), "child never wrote the ready-file"
    assert ready_file.read_bytes() == b"1", "child should have acquired the lock"

    p.join(timeout=10)
    assert not p.is_alive()
    assert p.exitcode != 0, "child should have os._exit(1) — exitcode should be non-zero"
    # Child exited — kernel should have released the flock.

    from lore_core.lockfile import try_acquire_spawn_lock

    with try_acquire_spawn_lock(lore_root, "a") as (held, _stamp):
        assert held, "parent should immediately acquire the lock after child's crash"


def test_cooldown_blocks_second_call_within_window(tmp_path: Path, monkeypatch) -> None:
    """Sequential: first call spawns, second within cooldown returns False."""
    lore_root = _make_lore_root(tmp_path)

    from unittest.mock import patch
    from lore_cli.hooks import _spawn_detached_curator_a

    with patch("subprocess.Popen"):
        first = _spawn_detached_curator_a(lore_root, cooldown_s=60)
        assert first is True, "first call should spawn"

        second = _spawn_detached_curator_a(lore_root, cooldown_s=60)
        assert second is False, "second call within cooldown must NOT spawn"


def test_cooldown_expired_allows_spawn(tmp_path: Path) -> None:
    """Age the stamp past cooldown → next call spawns."""
    lore_root = _make_lore_root(tmp_path)
    stamp = lore_root / ".lore" / "curator-a.spawn.stamp"
    stamp.parent.mkdir(parents=True, exist_ok=True)
    # Write a stamp timestamp from 2 hours ago.
    stamp.write_text(f"{time.time() - 7200:.6f}")

    from unittest.mock import patch
    from lore_cli.hooks import _spawn_detached_curator_a

    with patch("subprocess.Popen") as popen:
        result = _spawn_detached_curator_a(lore_root, cooldown_s=60)
        assert result is True, "expired cooldown must allow spawn"
        assert popen.call_count == 1


def test_spawn_failure_releases_lock(tmp_path: Path) -> None:
    """If Popen raises, the lock is released so the next call can acquire.

    Cooldown still blocks the next call (the stamp wasn't written because
    spawn failed — but we test lock-release by directly acquiring via the
    context manager after the failed spawn.)
    """
    lore_root = _make_lore_root(tmp_path)

    from unittest.mock import patch
    from lore_cli.hooks import _spawn_detached_curator_a

    with patch("subprocess.Popen", side_effect=OSError("fake spawn failure")):
        result = _spawn_detached_curator_a(lore_root, cooldown_s=60)
        assert result is False

    # Lock must be released — we can acquire it now.
    from lore_core.lockfile import try_acquire_spawn_lock
    with try_acquire_spawn_lock(lore_root, "a") as (held, _stamp):
        assert held, "lock should be released after spawn failure"


def test_legacy_stamp_migration_unlinks_old_files(tmp_path: Path) -> None:
    """Pre-existing last-curator-a-spawn file gets unlinked on first spawn."""
    lore_root = _make_lore_root(tmp_path)
    legacy = lore_root / ".lore" / "last-curator-a-spawn"
    legacy.write_text(f"{time.time():.6f}")
    assert legacy.exists()

    from unittest.mock import patch
    from lore_cli.hooks import _spawn_detached_curator_a

    with patch("subprocess.Popen"):
        _spawn_detached_curator_a(lore_root, cooldown_s=60)

    assert not legacy.exists(), "legacy stamp must be unlinked after first new-throttle spawn"


def test_per_role_locks_are_independent(tmp_path: Path) -> None:
    """Curator A lock does not block Curator B spawn, and vice versa."""
    lore_root = _make_lore_root(tmp_path)

    from unittest.mock import patch
    from lore_cli.hooks import _spawn_detached_curator_a, _spawn_detached_curator_b

    with patch("subprocess.Popen") as popen:
        a_result = _spawn_detached_curator_a(lore_root, cooldown_s=60)
        b_result = _spawn_detached_curator_b(lore_root, "testwiki", cooldown_s=60)

    assert a_result is True
    assert b_result is True
    assert popen.call_count == 2, "both roles should have independently spawned"
