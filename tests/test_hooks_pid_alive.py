"""Tests for ``lore_cli.hooks._pid_alive``.

Phase 3 of the cleanup roadmap fixed a cross-platform bug: the original
implementation walked ``/proc`` and returned ``True`` conservatively on
non-Linux systems, meaning macOS hosts never GC'd stale per-PID session
caches. The new ``os.kill(pid, 0)`` form works on all POSIX platforms
and gives an honest answer.
"""
from __future__ import annotations

import os

import pytest

from lore_cli.hooks import _pid_alive


def test_self_pid_is_alive() -> None:
    """The current process is the most reliable alive PID."""
    assert _pid_alive(os.getpid()) is True


def test_zero_and_negative_pids_are_dead() -> None:
    """Defensive: explicit guard against PID 0 (kernel idle) and negatives.

    ``os.kill(0, 0)`` would broadcast to the process group; ``os.kill(-pid, ...)``
    addresses a process group too. We never want either, so guard upstream.
    """
    assert _pid_alive(0) is False
    assert _pid_alive(-1) is False


def test_dead_pid_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """ProcessLookupError (ESRCH) means the PID is not in use."""
    def _raise_esrch(_pid: int, _sig: int) -> None:
        raise ProcessLookupError(3, "No such process")

    monkeypatch.setattr(os, "kill", _raise_esrch)
    assert _pid_alive(99999) is False


def test_permission_denied_means_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    """EPERM means the process exists but we can't signal it (owned by
    another user / sandboxed). We still know it's alive."""
    def _raise_eperm(_pid: int, _sig: int) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "kill", _raise_eperm)
    assert _pid_alive(1) is True


def test_other_oserror_is_conservatively_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    """If we can't probe at all, don't GC the cache. Conservative-True is
    the safe default for the GC use case (worst case: a stale cache
    sticks around for the 14-day max-age fallback)."""
    def _raise_other(_pid: int, _sig: int) -> None:
        raise OSError(22, "Some other failure")

    monkeypatch.setattr(os, "kill", _raise_other)
    assert _pid_alive(1) is True


def test_real_dead_pid_returns_false() -> None:
    """End-to-end check using a PID that's almost certainly never in use.

    The Linux kernel reserves PID 0 for the scheduler and PID 1 for init;
    this picks an upper-bound that the kernel will refuse for any real
    process (PID_MAX defaults are 2^15 on 32-bit and 2^22 on 64-bit, but
    ``2**31 - 1`` is well past either). Skipped if some lunatic has it.
    """
    sentinel = 2_147_483_646
    if _pid_alive(sentinel):
        pytest.skip(f"PID {sentinel} happens to be in use; can't test")
    assert _pid_alive(sentinel) is False
