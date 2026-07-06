"""Tests for lore_curator.reaper — liveness reaper."""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from lore_core.types import Turn
from lore_core.wiki_config import WikiConfig
from lore_curator.buffer_append import append_chunk
from lore_curator.buffer_store import OwnerInfo, Sidecar
from lore_curator.reaper import (
    is_owner_alive,
    reap_once,
)


def _make_turns(n: int = 2) -> list[Turn]:
    return [
        Turn(index=i, timestamp=None, role="user" if i % 2 == 0 else "assistant", text="x")
        for i in range(n)
    ]


@pytest.fixture
def lore_root(tmp_path: Path) -> Path:
    (tmp_path / ".lore" / "buffers").mkdir(parents=True)
    return tmp_path


@pytest.fixture(autouse=True)
def patch_collectors(monkeypatch):
    monkeypatch.setattr(
        "lore_curator.session_activity.collect_commits_by_sha", lambda *a, **kw: []
    )
    monkeypatch.setattr(
        "lore_curator.session_activity.collect_issues_in_window", lambda *a, **kw: ([], [])
    )
    monkeypatch.setattr(
        "lore_curator.session_activity.collect_projects_for_session", lambda **kw: []
    )
    monkeypatch.setattr("lore_core.git.git_repo_root", lambda cwd: None)
    monkeypatch.setattr("lore_core.git.current_repo", lambda cwd: "")


def _seed(lore_root: Path, **append_kw) -> tuple:
    """Seed one buffer; return (buffer, sidecar)."""
    outcome = append_chunk(
        lore_root=lore_root, chunk_turns=_make_turns(2), local_date="2026-05-01",
        transcript_id=append_kw.pop("transcript_id", "abc"),
        integration="claude-code", wiki="private", scope="proj:x",
        cwd=lore_root, wiki_root=lore_root / "wiki" / "private", cfg=WikiConfig(),
        **append_kw,
    )
    return outcome.buffer, outcome.buffer.read_sidecar()


# ---------------------------------------------------------------------------
# is_owner_alive
# ---------------------------------------------------------------------------


def test_owner_alive_local_pid_returns_true_or_none():
    """Real-process check on the test runner's pid -- should be True or None
    (None when /proc isn't readable, e.g. macOS)."""
    sidecar = Sidecar(owner=OwnerInfo(
        pid=os.getpid(),
        host="x",
        start_ts=0.0,
    ))
    # Force host match.
    import socket
    sidecar.owner.host = socket.gethostname()
    verdict = is_owner_alive(sidecar)
    assert verdict in (True, None)  # depends on /proc availability


def test_owner_gone_same_host_returns_none():
    """A same-host owner whose pid is absent is uncertain, not dead.

    The buffer's owner pid is re-stamped every heartbeat to the hook
    subprocess, which exits within milliseconds -- "pid gone" is the
    normal steady state, not a death signal, so long as the host still
    matches."""
    sidecar = Sidecar(owner=OwnerInfo(pid=2**31 - 1, host="", start_ts=0.0))
    import socket

    sidecar.owner.host = socket.gethostname()
    assert is_owner_alive(sidecar) is None


def test_owner_pid_reused_start_ts_mismatch_returns_none():
    """A live pid whose start-tick doesn't match the recorded value means
    the pid was reused by an unrelated process -- uncertain, not dead."""
    sidecar = Sidecar(owner=OwnerInfo(pid=os.getpid(), host="", start_ts=999999999.0))
    import socket

    sidecar.owner.host = socket.gethostname()
    assert is_owner_alive(sidecar) is None


def test_owner_different_host_returns_false():
    sidecar = Sidecar(owner=OwnerInfo(pid=1, host="other-machine", start_ts=0.0))
    assert is_owner_alive(sidecar) is False


# ---------------------------------------------------------------------------
# reap_once
# ---------------------------------------------------------------------------


def test_alive_owner_not_reaped(lore_root, monkeypatch):
    _seed(lore_root)
    # Force is_owner_alive to True regardless.
    monkeypatch.setattr("lore_curator.reaper.is_owner_alive", lambda s, **kw: True)
    report = reap_once(lore_root)
    assert report.scanned == 1
    assert report.alive == 1
    assert report.force_flushed == 0


def test_dead_owner_and_stale_is_reaped(lore_root, monkeypatch):
    buf, _ = _seed(lore_root)
    # Backdate last_heartbeat past the staleness threshold.
    stale = (datetime.now(UTC) - timedelta(hours=3)).isoformat().replace("+00:00", "Z")
    with buf.with_lock():
        buf.patch(last_heartbeat=stale, last_appended_at=stale)
    monkeypatch.setattr("lore_curator.reaper.is_owner_alive", lambda s, **kw: False)

    spawned: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        "lore_curator.reaper.spawn_detached_flush",
        lambda buffer_path, lore_root: spawned.append((buffer_path, lore_root)) or True,
    )
    report = reap_once(lore_root)
    assert report.force_flushed == 1
    assert spawned and spawned[0][0] == buf.sidecar_path


def test_dead_owner_reaped_immediately_even_with_fresh_heartbeat(lore_root, monkeypatch):
    """Owner pid unambiguously dead → reap regardless of heartbeat freshness.

    Why: short Claude sessions that die without a clean SessionEnd would
    otherwise leave stub notes in 'synthesis pending' for the full
    staleness window (30+ min). Once the PID is provably gone, waiting
    buys nothing.
    """
    buf, _ = _seed(lore_root)
    monkeypatch.setattr("lore_curator.reaper.is_owner_alive", lambda s, **kw: False)

    spawned: list[Path] = []
    monkeypatch.setattr(
        "lore_curator.reaper.spawn_detached_flush",
        lambda buffer_path, lore_root: spawned.append(buffer_path) or True,
    )
    report = reap_once(lore_root)
    assert report.force_flushed == 1
    assert spawned == [buf.sidecar_path]


def test_uncertain_liveness_with_fresh_heartbeat_not_reaped(lore_root, monkeypatch):
    """``alive_verdict is None`` (uncertain — no /proc) keeps the
    staleness floor: don't false-positive reap on macOS / network-fs
    when the heartbeat is fresh."""
    buf, _ = _seed(lore_root)
    monkeypatch.setattr("lore_curator.reaper.is_owner_alive", lambda s, **kw: None)
    report = reap_once(lore_root)
    assert report.force_flushed == 0
    assert report.alive == 1


def test_dry_run_does_not_spawn(lore_root, monkeypatch):
    buf, _ = _seed(lore_root)
    stale = (datetime.now(UTC) - timedelta(hours=3)).isoformat().replace("+00:00", "Z")
    with buf.with_lock():
        buf.patch(last_heartbeat=stale, last_appended_at=stale)
    monkeypatch.setattr("lore_curator.reaper.is_owner_alive", lambda s, **kw: False)

    spawned: list = []
    monkeypatch.setattr(
        "lore_curator.reaper.spawn_detached_flush",
        lambda buffer_path, lore_root: spawned.append(buffer_path) or True,
    )
    report = reap_once(lore_root, dry_run=True)
    assert report.force_flushed == 0
    assert spawned == []
    assert any(reason == "dry-run-would-reap" for _, reason in report.skipped)


def test_closed_buffers_skipped(lore_root, monkeypatch):
    buf, _ = _seed(lore_root)
    with buf.with_lock():
        buf.transition("ready")
        buf.transition("flushing")
        buf.transition("closed")

    report = reap_once(lore_root)
    assert report.already_done == 1
    assert report.force_flushed == 0


def test_pid_gone_same_host_not_reaped_within_staleness(lore_root):
    """Regression for the mid-session false reap: a heartbeat stamps a
    pid that then exits (the normal buffer-and-flush steady state) --
    the reaper must not treat pid-gone alone as death while the
    heartbeat is still fresh. The buffer must survive as-is."""
    import socket

    buf, _ = _seed(lore_root)
    with buf.with_lock():
        buf.patch(owner=OwnerInfo(pid=2**31 - 1, host=socket.gethostname(), start_ts=0.0))

    report = reap_once(lore_root)
    assert report.force_flushed == 0
    assert report.alive == 1
    assert buf.read_sidecar().state == "accumulating"


def test_pid_gone_same_host_reaped_after_staleness(lore_root, monkeypatch):
    """A same-host pid-gone owner is still eventually reaped once the
    heartbeat goes stale -- uncertainty defers to the staleness floor,
    it doesn't suppress reaping forever."""
    import socket

    buf, _ = _seed(lore_root)
    stale = (datetime.now(UTC) - timedelta(hours=3)).isoformat().replace("+00:00", "Z")
    with buf.with_lock():
        buf.patch(
            owner=OwnerInfo(pid=2**31 - 1, host=socket.gethostname(), start_ts=0.0),
            last_heartbeat=stale,
            last_appended_at=stale,
        )

    spawned: list = []
    monkeypatch.setattr(
        "lore_curator.reaper.spawn_detached_flush",
        lambda buffer_path, lore_root: spawned.append(buffer_path) or True,
    )
    report = reap_once(lore_root)
    assert report.force_flushed == 1
    assert spawned == [buf.sidecar_path]


def test_max_per_pass_bounds_scan(lore_root, monkeypatch):
    for tid in ("a", "b", "c"):
        _seed(lore_root, transcript_id=tid)
    report = reap_once(lore_root, max_per_pass=2)
    assert report.scanned == 2
