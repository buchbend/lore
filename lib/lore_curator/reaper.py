"""Liveness reaper for the buffer-and-flush curator.

Walks live (non-``_done``) buffers and force-flushes any whose owner
has crashed or wandered off — the safety net for "session ended without
a clean SessionEnd hook firing" and "long heartbeat gap with no
activity". Cap-trip is the floor for size; the reaper is the floor for
time.

Liveness verdict:

* If the owner PID is unambiguously **dead** (host mismatch,
  ``ProcessLookupError`` from ``os.kill(pid, 0)``, or
  ``/proc/<pid>/stat`` start-ts mismatch indicating PID reuse) →
  **reap immediately**, regardless of heartbeat freshness. Waiting on
  a confirmed-dead owner buys nothing; short Claude sessions that die
  without SessionEnd would otherwise leave stub notes pending for the
  full staleness window.
* If the owner is unambiguously **alive** → keep waiting.
* If liveness is **uncertain** (no ``/proc`` access — macOS, network
  fs, sandbox) → fall back on the staleness threshold
  (``liveness_stale_threshold_s``, default 30 min, per-wiki
  configurable; doubled on macOS).

Concurrency:

- Per-buffer flock acquired non-blocking; we skip a buffer that another
  process holds rather than queueing.
- Per-buffer spawn-lock under ``<stem>.spawn.lock`` prevents two
  concurrent reapers from double-spawning a flush worker for the same
  buffer.
- Spawn role is ``a-flush`` (distinct from ``a``) so reaper-driven
  spawns don't stampede the regular curator-A spawn lock.
"""
from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from lore_core.wiki_config import load_wiki_config
from lore_curator.buffer_store import Buffer, Sidecar, iter_all
from lore_curator.synthesis import spawn_detached_flush

if TYPE_CHECKING:
    from lore_core.run_log import RunLogger


__all__ = ["ReaperReport", "reap_once", "is_owner_alive"]


_START_TS_TOLERANCE = 2.0


@dataclass
class ReaperReport:
    scanned: int = 0
    alive: int = 0
    already_done: int = 0
    force_flushed: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)


def _read_proc_start_ticks(pid: int) -> float | None:
    """Return ``/proc/<pid>/stat`` field 22, or ``None`` on macOS / missing."""
    try:
        with open(f"/proc/{pid}/stat", "r") as fh:
            content = fh.read()
    except (FileNotFoundError, PermissionError, OSError):
        return None
    rparen = content.rfind(")")
    if rparen < 0:
        return None
    fields = content[rparen + 1 :].split()
    if len(fields) < 20:
        return None
    try:
        return float(fields[19])
    except ValueError:
        return None


def is_owner_alive(sidecar: Sidecar, *, host: str | None = None) -> bool | None:
    """Return True (alive), False (dead), or None (can't tell — keep waiting).

    Encapsulates the AND-condition liveness check. Returns:

    * ``True`` if the owner is local AND PID is alive AND start_ts matches.
    * ``False`` if the owner is local AND PID is gone, OR start_ts mismatch.
    * ``False`` if owner host differs from this host (definitely not us).
    * ``None`` if we can't read /proc and the host matches — the
      caller should treat this as "uncertain, fall back on staleness
      threshold".
    """
    host = host or socket.gethostname()
    if not sidecar.owner.pid:
        return None
    if sidecar.owner.host and sidecar.owner.host != host:
        return False
    pid = sidecar.owner.pid
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Pid exists but we can't signal; treat as alive.
        return True
    except OSError:
        return None

    expected = sidecar.owner.start_ts
    actual = _read_proc_start_ticks(pid)
    if actual is None or expected <= 0:
        # No /proc access — caller falls back on staleness threshold.
        return None
    if abs(actual - expected) > _START_TS_TOLERANCE:
        return False
    return True


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _parse_iso(stamp: str) -> datetime | None:
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_stale(sidecar: Sidecar, *, threshold_s: int, now: datetime) -> bool:
    last = _parse_iso(sidecar.last_heartbeat) or _parse_iso(sidecar.last_appended_at)
    if last is None:
        # Unknown last_heartbeat - be conservative and treat as fresh.
        return False
    age = (now - last).total_seconds()
    return age > threshold_s


def _staleness_threshold(buffer: Buffer, *, default_s: int) -> int:
    """Resolve the per-wiki staleness threshold via ``CuratorConfig``.

    Falls back to ``default_s`` when the wiki config can't be loaded.
    macOS fallback: doubled when ``/proc/<pid>/stat`` is unavailable
    (handled by the caller, not here).
    """
    sidecar = buffer.read_sidecar()
    if sidecar is None or not sidecar.wiki:
        return default_s
    wiki_dir = buffer.lore_root / "wiki" / sidecar.wiki
    try:
        cfg = load_wiki_config(wiki_dir)
        return int(cfg.curator.liveness_stale_threshold_s)
    except Exception:  # noqa: BLE001
        return default_s


def reap_once(
    lore_root: Path,
    *,
    dry_run: bool = False,
    max_per_pass: int | None = None,
    logger: "RunLogger | None" = None,
    now: datetime | None = None,
) -> ReaperReport:
    """One reaper pass over live buffers under ``<lore_root>/.lore/buffers``.

    ``max_per_pass`` bounds how many buffers we examine in one pass so
    inline calls from ``run_curator_a`` stay sub-100ms even with a
    pathological number of orphan buffers; the CLI passes ``None`` for
    "scan everything".

    Returns a :class:`ReaperReport` with counters + a per-skip reason
    list. Never raises — every per-buffer failure is captured in
    ``report.skipped``.
    """
    now = now or _now_utc()
    host = socket.gethostname()
    report = ReaperReport()

    for buf in iter_all(lore_root):
        if max_per_pass is not None and report.scanned >= max_per_pass:
            break
        report.scanned += 1
        try:
            verdict, reason = _judge(
                buf, host=host, now=now, logger=logger,
            )
        except Exception as exc:  # noqa: BLE001 - never let one buffer abort the pass
            report.skipped.append((buf.stem, f"judge-error: {type(exc).__name__}: {exc}"))
            continue

        if verdict == "alive":
            report.alive += 1
            continue
        if verdict == "closed":
            report.already_done += 1
            continue
        if verdict == "skip":
            report.skipped.append((buf.stem, reason))
            continue

        # verdict == "reap"
        if dry_run:
            report.skipped.append((buf.stem, "dry-run-would-reap"))
            continue
        spawned = spawn_detached_flush(buf.sidecar_path, lore_root=lore_root)
        if spawned:
            report.force_flushed += 1
            if logger is not None:
                logger.emit(
                    "reaper-force-flushed",
                    buffer_stem=buf.stem,
                    reason=reason,
                )
        else:
            report.skipped.append((buf.stem, "spawn-locked"))
    if logger is not None:
        logger.emit(
            "reaper-scanned",
            scanned=report.scanned,
            alive=report.alive,
            already_done=report.already_done,
            force_flushed=report.force_flushed,
        )
    return report


def _judge(
    buf: Buffer,
    *,
    host: str,
    now: datetime,
    logger: "RunLogger | None",
) -> tuple[str, str]:
    """Return ``(verdict, reason)`` for a single buffer.

    Verdicts: ``"alive"`` | ``"closed"`` | ``"skip"`` | ``"reap"``.
    """
    # Try the per-buffer flock non-blocking; if held, another process is
    # actively writing — definitely alive.
    with buf.with_lock(blocking=False) as held:
        if not held:
            return "alive", "lock-held"
        sidecar = buf.read_sidecar()
        if sidecar is None:
            return "skip", "no-sidecar"
        if sidecar.state == "closed":
            return "closed", "state-closed"

        threshold = _staleness_threshold(buf, default_s=1800)
        # macOS: no /proc; double the staleness threshold.
        if not Path(f"/proc/{sidecar.owner.pid}/stat").exists():
            threshold = max(threshold * 2, threshold)

        alive_verdict = is_owner_alive(sidecar, host=host)

        if alive_verdict is True:
            # Owner is unambiguously alive on this host — keep waiting.
            return "alive", "owner-alive"

        if alive_verdict is False:
            # Owner is unambiguously dead — reap regardless of staleness.
            # Why: short Claude sessions that die without SessionEnd leave
            # stub notes "synthesis pending" for the full staleness window
            # (30+ min); waiting buys nothing once the PID is provably gone.
            return "reap", "owner-dead"

        # alive_verdict is None: uncertain (no /proc / network-fs / macOS).
        # Fall back on staleness threshold to avoid false-positive reaps.
        if not _is_stale(sidecar, threshold_s=threshold, now=now):
            return "alive", "fresh-heartbeat"
        return "reap", "stale+owner-uncertain"
