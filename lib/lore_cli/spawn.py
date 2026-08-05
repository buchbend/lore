"""Detached subprocess machinery for the curator/transcript-sync hooks.

This module owns the spawn-side safety net that the v0.37 hang storm forced
us to build:

* per-role flock + cooldown stamps so back-to-back hook events don't pile
  parallel children onto the same broken state;
* a runaway gate that refuses fresh spawns when the prior child for a role
  is still alive past ``cooldown_s * 5``;
* atomic log + meta-sidecar rotation so each generation is recoverable
  after a crash;
* a tiny ``SpawnRole`` registry + a single ``spawn(role, lore_root,
  **extra)`` entry point so adding a new background process is one row.

The legacy ``_spawn_detached_curator_{a,b,c}`` and
``_spawn_detached_transcript_sync`` wrappers stay as one-liners that
delegate to :func:`spawn` — existing call sites and test patch points
keep working unchanged.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from lore_core.spine import ErrorCode, emit_hook_event

# ---------------------------------------------------------------------------
# Stamp primitives — used by spawn cooldowns AND the heartbeat throttles in
# ``hooks.py`` (re-imported there).
# ---------------------------------------------------------------------------


def _stamp_within_cooldown(stamp: Path, cooldown_s: int) -> bool:
    """True if stamp exists and is younger than cooldown_s seconds."""
    import time as _time

    try:
        last = float(stamp.read_text().strip())
    except (OSError, ValueError):
        return False
    return (_time.time() - last) < cooldown_s


def _write_stamp(stamp: Path) -> None:
    """Atomic write of current unix timestamp into stamp. Best-effort."""
    import time as _time

    stamp.parent.mkdir(parents=True, exist_ok=True)
    tmp = stamp.with_suffix(stamp.suffix + ".tmp")
    tmp.write_text(f"{_time.time():.6f}")
    os.replace(tmp, stamp)


# ---------------------------------------------------------------------------
# Process / log / sidecar machinery
# ---------------------------------------------------------------------------


def _open_proc_log(lore_root: Path, role: str, *, keep: int = 3) -> int | None:
    """Open .lore/proc/<role>.log for subprocess output, rotating previous generations."""
    import contextlib

    proc_dir = lore_root / ".lore" / "proc"
    try:
        proc_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    log_path = proc_dir / f"{role}.log"
    with contextlib.suppress(OSError):
        (proc_dir / f"{role}.log.{keep}").unlink(missing_ok=True)
    for i in range(keep, 1, -1):
        src = proc_dir / f"{role}.log.{i - 1}"
        dst = proc_dir / f"{role}.log.{i}"
        with contextlib.suppress(OSError):
            os.replace(str(src), str(dst))
    if log_path.exists():
        with contextlib.suppress(OSError):
            os.replace(str(log_path), str(proc_dir / f"{role}.log.1"))
    try:
        return os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    except OSError:
        return None


def _rotate_meta_sidecar(proc_dir: Path, role: str, *, keep: int = 3) -> None:
    """Rotate <role>.meta.json alongside proc logs. Best-effort."""
    import contextlib

    with contextlib.suppress(OSError):
        (proc_dir / f"{role}.meta.json.{keep}").unlink(missing_ok=True)
    for i in range(keep, 1, -1):
        src = proc_dir / f"{role}.meta.json.{i - 1}"
        dst = proc_dir / f"{role}.meta.json.{i}"
        with contextlib.suppress(OSError):
            os.replace(str(src), str(dst))
    current = proc_dir / f"{role}.meta.json"
    if current.exists():
        with contextlib.suppress(OSError):
            os.replace(str(current), str(proc_dir / f"{role}.meta.json.1"))


def _process_is_ours(pid: int) -> bool:
    """True if ``pid`` is alive AND looks like a lore_cli process.

    On Linux the cmdline check via ``/proc/<pid>/cmdline`` dodges PID-recycle
    false positives — the kernel reuses PIDs aggressively, so a bare
    ``os.kill(pid, 0)`` on a stale meta.json could match an unrelated
    process. On non-Linux the cmdline file isn't present and we fall back
    to the liveness probe alone (the rare false positive self-heals once
    the next successful spawn rewrites meta.json).
    """
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    cmdline_path = Path(f"/proc/{pid}/cmdline")
    if not cmdline_path.exists():
        return True  # non-Linux fallback
    try:
        cmdline = cmdline_path.read_bytes().replace(b"\x00", b" ")
    except OSError:
        return True  # alive but unreadable; assume ours
    return b"lore_cli" in cmdline


def _prior_spawn_runaway(lore_root: Path, role: str, *, runaway_age_s: int) -> dict | None:
    """Return runaway-process info if the prior spawn for ``role`` is hung.

    "Hung" = sidecar meta.json says ``exit_code is None`` AND its recorded
    ``pid`` is still alive on this host AND ``start_ts`` is older than
    ``runaway_age_s`` seconds.

    Returns ``None`` (safe to spawn) when meta.json is absent, malformed,
    already exited, or the prior process is young/dead. The returned dict
    carries ``pid``, ``age_s``, ``start_ts`` for telemetry.

    Final gate before ``_spawn_detached`` commits a fresh subprocess —
    catches the pile-up pattern where a child hangs (e.g. the v0.37.0 lock
    spin) and the cooldown stamp keeps green-lighting new spawns on the
    same broken state. See issue #42 for the related lockfile silent-
    cleanup follow-up.
    """
    import time as _time

    meta_path = lore_root / ".lore" / "proc" / f"{role}.meta.json"
    try:
        meta = json.loads(meta_path.read_text())
    except (OSError, ValueError):
        return None
    if meta.get("exit_code") is not None:
        return None
    pid = meta.get("pid")
    start_ts = meta.get("start_ts")
    if not isinstance(pid, int) or not isinstance(start_ts, (int, float)):
        return None
    age_s = _time.time() - start_ts
    if age_s < runaway_age_s:
        return None
    if not _process_is_ours(pid):
        return None
    return {"pid": pid, "age_s": int(age_s), "start_ts": start_ts}


def _spawn_detached(
    lore_root: Path,
    role: str,
    cmd: list[str],
    *,
    cooldown_s: int,
    runaway_age_s: int | None = None,
) -> bool:
    """Fire-and-forget a subprocess under a spawn lock + cooldown stamp.

    Acquires a non-blocking flock on the per-role spawn lock. Returns False
    if another process holds the lock OR the cooldown stamp is still fresh
    OR the prior spawn for this role is still alive past the runaway
    threshold (default ``cooldown_s * 5``).

    The runaway gate is the safety net for "child hangs and cooldown keeps
    green-lighting fresh spawns" — once tripped, a single warning event is
    appended to the event spine per ``cooldown_s * 10`` window
    (throttled via ``curator-<role>.runaway.stamp``) so users running
    ``lore status`` / grepping the log can see the issue without it
    spamming on every UserPromptSubmit.
    """
    import contextlib
    import subprocess

    from lore_core.lockfile import try_acquire_spawn_lock

    effective_runaway = runaway_age_s if runaway_age_s is not None else cooldown_s * 5

    with try_acquire_spawn_lock(lore_root, role) as (held, stamp):
        if not held:
            return False
        if _stamp_within_cooldown(stamp, cooldown_s):
            return False
        runaway = _prior_spawn_runaway(lore_root, role, runaway_age_s=effective_runaway)
        if runaway is not None:
            warn_stamp = lore_root / ".lore" / f"curator-{role}.runaway.stamp"
            if not _stamp_within_cooldown(warn_stamp, cooldown_s * 10):
                try:
                    emit_hook_event(
                        lore_root,
                        event="spawn-throttle",
                        outcome="prior-runaway",
                        error_code=ErrorCode.SPAWN_RUNAWAY,
                        role=role,
                        error={
                            "type": "PriorSpawnAlive",
                            "pid": runaway["pid"],
                            "age_s": runaway["age_s"],
                            "runaway_threshold_s": effective_runaway,
                        },
                    )
                except Exception:
                    pass
                with contextlib.suppress(OSError):
                    _write_stamp(warn_stamp)
            return False
        env = os.environ.copy()
        # Re-inject as LORE_ROOT so child processes resolve identically
        # without re-reading ~/.config/lore/config.yml. The child's
        # lore_root_source() will report "env" even if the parent
        # resolved via config — that's intentional. Resolution-source
        # provenance is per-process; the path value is what matters
        # across boundaries.
        env["LORE_ROOT"] = str(lore_root)
        env["LORE_CURATOR_MODE"] = "1"
        log_fd = _open_proc_log(lore_root, role)
        proc_dir = lore_root / ".lore" / "proc"
        meta_path = proc_dir / f"{role}.meta.json"
        _rotate_meta_sidecar(proc_dir, role)
        wrapped_cmd = [
            sys.executable,
            "-m",
            "lore_cli._proc_wrapper",
            str(meta_path),
            "--",
            *cmd,
        ]
        try:
            subprocess.Popen(
                wrapped_cmd,
                cwd=str(lore_root),
                start_new_session=True,
                stdout=log_fd if log_fd is not None else subprocess.DEVNULL,
                stderr=log_fd if log_fd is not None else subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                env=env,
            )
        except (OSError, subprocess.SubprocessError):
            if log_fd is not None:
                os.close(log_fd)
            return False
        if log_fd is not None:
            os.close(log_fd)
        with contextlib.suppress(OSError):
            _write_stamp(stamp)
        return True


# ---------------------------------------------------------------------------
# SpawnRole registry + the single public ``spawn()`` entry point
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpawnRole:
    """One row in the spawn-role registry.

    ``argv_builder`` is called with the keyword arguments forwarded from
    :func:`spawn`. The returned argv is the *child* command;
    ``_spawn_detached`` wraps it in ``python -m lore_cli._proc_wrapper``
    for sidecar lifecycle tracking.
    """

    name: str
    argv_builder: Callable[..., list[str]] = field(repr=False)
    default_cooldown_s: int


SPAWN_ROLES: dict[str, SpawnRole] = {
    "transcripts": SpawnRole(
        name="transcripts",
        argv_builder=lambda: [
            sys.executable,
            "-m",
            "lore_cli",
            "transcripts",
            "sync",
        ],
        default_cooldown_s=300,
    ),
}


def spawn(role: str, lore_root: Path, *, cooldown_s: int | None = None, **extra) -> bool:
    """Public entry point — fire-and-forget the registered subprocess for ``role``.

    Looks up :data:`SPAWN_ROLES`, builds the argv (forwarding ``**extra``
    to the role's ``argv_builder``), and dispatches through
    :func:`_spawn_detached` with the role's cooldown + stamp-migration
    flags. ``cooldown_s`` overrides the registry default when caller has
    a wiki-config-derived value.
    """
    role_def = SPAWN_ROLES[role]
    return _spawn_detached(
        lore_root,
        role_def.name,
        role_def.argv_builder(**extra),
        cooldown_s=(cooldown_s if cooldown_s is not None else role_def.default_cooldown_s),
    )


# ---------------------------------------------------------------------------
# Backward-compat wrappers — kept as one-liners over :func:`spawn` so
# existing call sites and `patch("lore_cli.hooks._spawn_detached_curator_*")`
# in the test suite keep working unchanged.
# ---------------------------------------------------------------------------


def _spawn_detached_transcript_sync(lore_root: Path, *, cooldown_s: int = 300) -> bool:
    """Fire-and-forget ``lore transcripts sync`` subprocess.

    Runs on the same spawn-lock + cooldown pattern as the curators, so
    a busy SessionStart hook can't stampede the filesystem with parallel
    sync jobs. The P4a sync itself is idempotent; the lock exists purely
    as a politeness budget.
    """
    return spawn("transcripts", lore_root, cooldown_s=cooldown_s)
